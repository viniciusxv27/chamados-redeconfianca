"""Módulo Contracheque no app: meus holerites, informes de rendimentos e assinatura.

Regras de onde vieram (contracheque/views.py):
- Entrada: as telas pessoais só têm @login_required (my_payslips, views.py:78) e o
  item do menu aparece para todo logado (templates/base.html, sem {% if %}). A
  trava de "logado e sem bloqueio" já vem do ``permissao('contracheque')``.
- Lista: só os do próprio (``Payslip.objects.filter(user=request.user)``, views.py:81;
  informes em views.py:992).
- Detalhe / PDF: o dono OU quem administra o módulo (``pode_administrar``, views.py:108,
  122, 174, 1004, 1018). Fora disso → 404 (não contamos que o id existe).
- Assinar: a própria função da web (``api_sign_payslip``, views.py:711), que só deixa
  o dono assinar e uma vez só.
- PDF: a própria view da web (``payslip_pdf``/``payslip_signed_pdf``/``income_report_pdf``),
  chamada por dentro com o dono do link assinado — mesma leitura do storage e mesmo
  recorte da página do funcionário.

Visões de administração (importar, distribuir, excluir, relatório) ficam no portal.
"""
import io
import json

from django.contrib.messages.storage.cookie import CookieStorage
from django.http import Http404
from django.test import RequestFactory
from django.urls import path
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from contracheque import views as web
from contracheque.models import IncomeReport, Payslip

from .. import dados
from ..arquivos import link, registrar
from ..autenticacao import TokenMobileAuthentication
from . import permissao


def pode_entrar(user):
    # contracheque/views.py:78 — só @login_required; o menu (base.html) aparece para todo logado.
    return bool(user and user.is_authenticated and user.is_active)


MODULO = {'rotulo': 'Contracheque', 'descricao': 'Holerites, informes e assinatura',
          'icone': 'receipt-outline', 'cor': '#27AE60', 'pode_entrar': pode_entrar}

_PERM = [permissao('contracheque')]
_AUTH = [TokenMobileAuthentication]


# ── Quem vê o quê (a régua da web) ───────────────────────────────────────────

def _pode_ver(user, dono_id):
    """contracheque/views.py:108 — dono ou quem administra (SUPERADMIN/ADMINISTRAÇÃO/rh.gestao)."""
    return dono_id == user.pk or web.pode_administrar(user)


def _holerite(user, pk):
    obj = Payslip.objects.select_related('user').filter(pk=pk).first()
    if obj is None or not _pode_ver(user, obj.user_id):
        raise Http404('Contracheque não encontrado.')
    return obj


def _informe(user, pk):
    obj = IncomeReport.objects.select_related('user').filter(pk=pk).first()
    if obj is None or not _pode_ver(user, obj.user_id):
        raise Http404('Informe não encontrado.')
    return obj


# ── Formato das respostas ────────────────────────────────────────────────────

def _dinheiro(valor):
    return None if valor is None else str(valor)


def _holerite_resumo(p):
    return {
        'id': p.pk,
        'mes': p.month,
        'ano': p.year,
        'competencia': p.period_display,
        'mes_nome': p.month_name,
        'liquido': _dinheiro(p.net_pay),
        'proventos': _dinheiro(p.total_earnings),
        'descontos': _dinheiro(p.total_deductions),
        'assinado': p.is_signed,
        'assinado_em': dados.iso(p.signed_at),
    }


def _itens(lista):
    saida = []
    for item in lista or []:
        if isinstance(item, dict):
            saida.append({'descricao': str(item.get('description') or ''), 'valor': str(item.get('value') or '')})
    return saida


def _holerite_detalhe(request, p):
    proprio = p.user_id == request.user.pk
    base = _holerite_resumo(p)
    base.update({
        'de_outra_pessoa': None if proprio else dados.pessoa(request, p.user),
        'funcionario': {
            'nome': p.employee_name or None,
            'cpf': p.cpf or None,
            'cargo': p.job_title or None,
            'admissao': p.admission_date or None,
            'departamento': p.department or None,
        },
        'salario_base': _dinheiro(p.base_salary),
        'itens_proventos': _itens(p.earnings_detail),
        'itens_descontos': _itens(p.deductions_detail),
        'bases': [
            {'rotulo': rotulo, 'valor': _dinheiro(valor)}
            for rotulo, valor in (('Base FGTS', p.fgts_base), ('Depósito FGTS', p.fgts_deposit),
                                  ('Base IRRF', p.irrf_base), ('Base INSS', p.inss_base))
            if valor
        ],
        # payslip_detail.html: a área de assinatura só aparece para o dono.
        'pode_assinar': proprio and not p.is_signed,
        'assinatura': ({
            'em': dados.iso(p.signed_at),
            'ip': p.signature_ip,
            'hash': (p.signature_hash[:24] + '…') if p.signature_hash else None,
            'imagem': p.signature_image or None,
        } if p.is_signed and proprio else None),
        'tem_pdf': bool(p.pdf_file and p.pdf_file.name),
        'pdf': link(request, 'contracheque', p.pk) if (p.pdf_file and p.pdf_file.name) else None,
        'pdf_assinado': link(request, 'contracheque_assinado', p.pk) if p.is_signed else None,
        'caminho_web': f'/contracheque/{p.pk}/',
    })
    return base


def _informe_resumo(r):
    return {
        'id': r.pk,
        'ano_base': r.base_year,
        'exercicio': r.exercise_year,
        'total_rendimentos': _dinheiro(r.total_rendimentos),
        'irrf': _dinheiro(r.irrf),
    }


def _informe_detalhe(request, r):
    proprio = r.user_id == request.user.pk
    base = _informe_resumo(r)

    def linhas(pares):
        return [{'rotulo': rotulo, 'valor': _dinheiro(valor)} for rotulo, valor in pares]

    # Mesmas seções e rótulos de templates/contracheque/income_report_detail.html.
    base.update({
        'de_outra_pessoa': None if proprio else dados.pessoa(request, r.user),
        'beneficiario': {'nome': r.employee_name or None, 'cpf': r.cpf or None},
        'total_isentos': _dinheiro(r.total_isentos),
        'secoes': [
            {'titulo': 'Rendimentos tributáveis, deduções e IRRF', 'linhas': linhas((
                ('Total dos rendimentos (inclusive férias)', r.total_rendimentos),
                ('Contribuição previdenciária oficial', r.contribuicao_previdenciaria),
                ('Contribuição previdência privada/FAPI', r.contribuicao_previdencia_privada),
                ('Pensão alimentícia', r.pensao_alimenticia),
                ('IRRF', r.irrf),
            ))},
            {'titulo': 'Rendimentos isentos e não tributáveis', 'linhas': linhas((
                ('Parcela isenta aposentadoria (65+)', r.parcela_isenta_aposentadoria),
                ('Parcela isenta 13º aposentadoria (65+)', r.parcela_isenta_13_aposentadoria),
                ('Diárias e ajuda de custo', r.diarias_ajuda_custo),
                ('Pensão/aposentadoria por moléstia grave', r.pensao_moletia_grave),
                ('Lucros e dividendos', r.lucros_dividendos),
                ('Valores pagos titular/sócio ME/EPP', r.valores_titular_socio),
                ('Indenizações rescisão/PDV', r.indenizacao_rescisao),
                ('Juros de mora', r.juros_mora),
                ('Outros', r.outros_isentos),
            ))},
            {'titulo': 'Rendimentos sujeitos à tributação exclusiva', 'linhas': linhas((
                ('13º salário', r.decimo_terceiro),
                ('IRRF sobre 13º', r.irrf_13),
                ('Outros', r.outros_exclusivos),
            ))},
        ],
        'pdf': link(request, 'contracheque_informe', r.pk) if r.pdf_file else None,
        'caminho_web': f'/contracheque/informes/{r.pk}/',
    })
    return base


# ── Endpoints ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def inicio(request):
    """Meus contracheques (todos os anos; o app agrupa por ano) + meus informes."""
    holerites = list(Payslip.objects.filter(user=request.user).order_by('-year', '-month'))
    informes = list(IncomeReport.objects.filter(user=request.user).order_by('-base_year'))
    return Response({
        'anos': sorted({p.year for p in holerites}, reverse=True),
        'pendentes': sum(1 for p in holerites if not p.is_signed),
        'contracheques': [_holerite_resumo(p) for p in holerites],
        'informes': [_informe_resumo(r) for r in informes],
        'administra': web.pode_administrar(request.user),
    })


@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def detalhe(request, pk):
    return Response(_holerite_detalhe(request, _holerite(request.user, pk)))


@api_view(['POST'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def assinar(request, pk):
    """Assina com a função da web (contracheque/views.py:711): o corpo é o mesmo {signature: data:image/png…}."""
    _holerite(request.user, pk)                    # quem não vê o holerite recebe 404, não 403
    resposta = web.api_sign_payslip(request._request, pk)
    try:
        corpo = json.loads(resposta.content or b'{}')
    except ValueError:
        corpo = {}
    if resposta.status_code != 200 or not corpo.get('success'):
        return Response({'erro': corpo.get('error') or 'Não foi possível assinar.'},
                        status=resposta.status_code if resposta.status_code >= 400 else 400)
    return Response(_holerite_detalhe(request, _holerite(request.user, pk)))


@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def informe(request, pk):
    return Response(_informe_detalhe(request, _informe(request.user, pk)))


urlpatterns = [
    path('', inicio, name='m_contracheque'),
    path('<int:pk>/', detalhe, name='m_contracheque_detalhe'),
    path('<int:pk>/assinar/', assinar, name='m_contracheque_assinar'),
    path('informes/<int:pk>/', informe, name='m_contracheque_informe'),
]


# ── PDF por link assinado ────────────────────────────────────────────────────

def _pdf_da_web(view, user, pk):
    """Chama a view de PDF do portal como o dono do link e devolve os bytes (ou 404)."""
    requisicao = RequestFactory().get('/')
    requisicao.user = user
    requisicao._messages = CookieStorage(requisicao)   # a view usa messages nos desvios
    resposta = view(requisicao, pk)
    if resposta.status_code != 200 or 'pdf' not in (resposta.get('Content-Type') or ''):
        raise Http404('PDF indisponível.')
    return io.BytesIO(resposta.content)


@registrar('contracheque')
def _pdf_holerite(user, obj_id):
    p = _holerite(user, int(obj_id))
    nome = f'contracheque_{p.year}_{p.month:02d}.pdf'
    return _pdf_da_web(web.payslip_pdf, user, p.pk), nome, 'application/pdf'


@registrar('contracheque_assinado')
def _pdf_holerite_assinado(user, obj_id):
    p = _holerite(user, int(obj_id))
    nome = f'contracheque_assinado_{p.year}_{p.month:02d}.pdf'
    return _pdf_da_web(web.payslip_signed_pdf, user, p.pk), nome, 'application/pdf'


@registrar('contracheque_informe')
def _pdf_informe(user, obj_id):
    r = _informe(user, int(obj_id))
    return _pdf_da_web(web.income_report_pdf, user, r.pk), f'informe_{r.base_year}.pdf', 'application/pdf'

