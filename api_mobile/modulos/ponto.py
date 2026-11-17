"""Módulo Ponto no app: Meu ponto (Tangerino) + Solicitações ao DP (/ponto/solicitacoes/).

Duas portas do portal num módulo só. A entrada é a soma das duas regras web:

- Solicitações ao DP: qualquer pessoa logada — ``@login_required`` em todas as views do
  colaborador (solicitacoes_dp/views.py:26, :62, :103) e o menu com
  ``{% if user.is_authenticated %}`` (templates/base.html:550).
- Meu ponto: ``users.module_access._gate_ponto`` → ``ConfiguracaoTangerino.libera``
  (users/module_access.py:280; menu em templates/base.html:506).

Como a primeira já vale para todo mundo logado, o módulo aparece para todos; cada seção
(marcações, férias, caixa do DP) responde pela própria regra e o ``resumo/`` diz quais
estão abertas para a pessoa.

Escrita: só o que a web faz pelas mesmas funções de ``solicitacoes_dp.servicos`` (criar,
corrigir, cancelar, comentar e, para quem analisa, assumir/aprovar/devolver/lançar).
Os avisos dessas funções são do sino do portal (sem push, sem WhatsApp). Nada aqui bate
ponto nem escreve na Sólides.
"""
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from rest_framework.decorators import (api_view, authentication_classes, parser_classes,
                                       permission_classes)
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from .. import dados
from ..arquivos import link, registrar
from ..autenticacao import TokenMobileAuthentication
from . import permissao
from . import ponto_relogio as relogio


def _solicitacoes_liberadas(user):
    # solicitacoes_dp/views.py: só @login_required (a mesma condição do menu, templates/base.html:550).
    return bool(user and getattr(user, 'is_authenticated', False) and user.is_active)


def pode_entrar(user):
    """Entra em /ponto/solicitacoes/ (todo logado) OU no Meu ponto (gate 'ponto' do portal)."""
    from users.module_access import tem_acesso
    return _solicitacoes_liberadas(user) or tem_acesso(user, 'ponto')


MODULO = {'rotulo': 'Ponto', 'descricao': 'Marcações, solicitações ao DP e férias',
          'icone': 'time-outline', 'cor': '#3B82F6', 'pode_entrar': pode_entrar}

TAMANHO_PAGINA = 20


def _S():
    from solicitacoes_dp.models import Solicitacao
    return Solicitacao


def _erro_regra(erro):
    erros = list(erro.args[0]) if erro.args and isinstance(erro.args[0], (list, tuple)) else [str(erro)]
    return Response({'erro': '\n'.join(erros), 'erros': erros}, status=400)


def _sim(valor):
    return str(valor or '').strip().lower() in ('1', 'true', 'sim', 'on')


def _pagina(request, queryset):
    from ..views import _pagina as pagina
    return pagina(request, queryset)


# ── Formato ──────────────────────────────────────────────────────────────────

def _resumo(request, sol, com_colaborador=False):
    saida = {
        'id': sol.pk,
        'tipo': sol.tipo,
        'tipo_nome': sol.get_tipo_display(),
        'titulo': sol.titulo,
        'status': sol.status,
        'status_nome': sol.get_status_display(),
        'aberta': sol.aberta,
        'data': sol.data.isoformat(),
        'periodo': sol.periodo,
        'enviada_em': dados.iso(sol.enviada_em),
        'dias_esperando': sol.dias_esperando,
        'ajustado_no_ponto': sol.ajustado_no_ponto,
        'anexos': len([a for a in sol.anexos.all() if not a.removido]),
    }
    if com_colaborador:
        saida['colaborador'] = dados.pessoa(request, sol.colaborador)
        saida['responsavel'] = dados.pessoa(request, sol.responsavel)
    return saida


def _anexo(request, a):
    return {'id': a.pk, 'nome': a.nome_original or 'anexo', 'tipo': a.content_type or '', 'tamanho': a.tamanho,
            'imagem': a.e_imagem, 'pdf': a.e_pdf, 'removido': a.removido, 'enviado_em': dados.iso(a.enviado_em),
            'url': link(request, 'ponto_anexo', a.pk)}


def _detalhe(request, sol):
    """O que solicitacoes_dp/views.py:detalhe (linha 120) põe na tela, com as mesmas flags de ação."""
    from solicitacoes_dp import servicos
    from solicitacoes_dp.permissoes import e_analista
    S = _S()
    user = request.user
    dp = e_analista(user)
    dono = sol.colaborador_id == user.pk
    registrado = servicos.ponto_registrado(sol.colaborador, sol.data) if sol.tipo == S.AJUSTE else None
    outras = (S.objects.filter(colaborador=sol.colaborador).exclude(pk=sol.pk).prefetch_related('anexos')
              .order_by('-enviada_em')[:5] if dp else [])
    anexos = list(sol.anexos.all())
    return {
        **_resumo(request, sol, com_colaborador=True),
        'especie': sol.especie or None,
        'especie_nome': sol.get_especie_display() or None,
        'data_fim': dados.iso(sol.data_fim),
        'hora_inicio': sol.hora_inicio.strftime('%H:%M') if sol.hora_inicio else None,
        'hora_fim': sol.hora_fim.strftime('%H:%M') if sol.hora_fim else None,
        'dias': sol.dias,
        'motivo_ajuste': sol.motivo_ajuste or None,
        'motivo_nome': sol.get_motivo_ajuste_display() or None,
        'marcacoes': [{'rotulo': m.get('rotulo', ''), 'hora': m.get('hora', '')} for m in sol.marcacoes or []],
        'justificativa': sol.justificativa,
        'resposta': sol.resposta or None,
        'ajustado_em': dados.iso(sol.ajustado_em),
        'aprovada_em': dados.iso(sol.aprovada_em),
        'registrado': registrado,
        'anexos_lista': [_anexo(request, a) for a in anexos if not a.removido],
        'anexos_removidos': [_anexo(request, a) for a in anexos if a.removido],
        'eventos': [{'id': e.pk, 'acao': e.acao, 'acao_nome': e.get_acao_display(),
                     'autor': dados.pessoa(request, e.autor), 'texto': e.texto or None,
                     'alteracoes': e.alteracoes or [], 'em': dados.iso(e.em)}
                    for e in sol.eventos.select_related('autor', 'autor__sector')],
        'outras': [_resumo(request, o) for o in outras],
        'dono': dono,
        'dp': dp,
        'pode_corrigir': dono and sol.status in (S.DEVOLVIDA, S.ENVIADA),
        'pode_cancelar': dono and sol.status in (S.ENVIADA, S.DEVOLVIDA),
        'pode_analisar': dp and sol.status in (S.ENVIADA, S.EM_ANALISE),
        'pode_assumir': dp and sol.status == S.ENVIADA,
        'pode_lancar': dp and sol.tipo == S.AJUSTE and sol.status == S.APROVADA and not sol.ajustado_no_ponto,
        'pode_comentar': dono or dp,
        'url_web': f'/ponto/solicitacoes/{sol.pk}/',
    }


def _solicitacao_visivel(request, pk):
    """Mesma trava de solicitacoes_dp/views.py:111 (_solicitacao_visivel): dono ou quem analisa; senão 404."""
    from solicitacoes_dp.permissoes import pode_ver
    sol = get_object_or_404(_S().objects.select_related('colaborador', 'colaborador__sector', 'responsavel',
                                                        'responsavel__sector'), pk=pk)
    if not pode_ver(request.user, sol):
        raise Http404
    return sol


@registrar('ponto_anexo')
def _arquivo_anexo(user, obj_id):
    """Anexo da solicitação: a mesma conta de solicitacoes_dp/views.py:anexo (dono ou quem analisa)."""
    from solicitacoes_dp.models import Anexo
    from solicitacoes_dp.permissoes import pode_ver
    anexo = Anexo.objects.select_related('solicitacao').get(pk=int(obj_id))
    if not pode_entrar(user) or not pode_ver(user, anexo.solicitacao):
        raise PermissionDenied
    return anexo.arquivo, anexo.nome_original or 'anexo', anexo.content_type or None


# ── Endpoints ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
def resumo(request):
    """Quais seções a pessoa abre, e os contadores (sem chamar o Tangerino: é rápido)."""
    from solicitacoes_dp.permissoes import e_analista
    S = _S()
    user = request.user
    config = relogio._config()
    contagem = dict(S.objects.filter(colaborador=user).values_list('status').annotate(n=Count('id'))
                    .values_list('status', 'n'))
    dp = e_analista(user)
    return Response({
        'secoes': {
            'marcacoes': relogio.ponto_liberado(user, config),
            'ferias': relogio.ferias_liberadas(user, config),
            'solicitacoes': _solicitacoes_liberadas(user),
            'escala': True,
            'caixa_dp': dp,
        },
        'vinculado': bool(user.tangerino_employee_id),
        'solicitacoes': {'abertas': sum(contagem.get(s, 0) for s in S.ABERTOS),
                         'devolvidas': contagem.get(S.DEVOLVIDA, 0),
                         'total': sum(contagem.values())},
        'dp_pendentes': (S.objects.filter(status__in=(S.ENVIADA, S.EM_ANALISE)).count() if dp else 0),
    })


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
def formulario(request):
    """As opções do formulário (solicitacoes_dp/views.py:_contexto_form) e os limites de servicos.py."""
    from solicitacoes_dp import servicos
    S = _S()

    def opcoes(lista):
        return [{'valor': v, 'rotulo': r} for v, r in lista]

    return Response({
        'tipos': opcoes(S.TIPOS),
        'especies': {S.ATESTADO: opcoes(S.ESPECIES_ATESTADO), S.DECLARACAO: opcoes(S.ESPECIES_DECLARACAO)},
        'motivos': opcoes(S.MOTIVOS_AJUSTE),
        'rotulos_marcacao': list(servicos.ROTULOS_MARCACAO),
        'extensoes': list(servicos.EXTENSOES),
        'tamanho_max_mb': servicos.TAMANHO_MAX_MB,
        'anexos_max': servicos.ANEXOS_MAX,
        'dias_para_tras_ajuste': servicos.DIAS_PARA_TRAS_AJUSTE,
        'dias_atestado_max': servicos.DIAS_ATESTADO_MAX,
        'hoje': timezone.localdate().isoformat(),
    })


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
def marcacoes_do_dia(request):
    """O que o relógio registrou no dia (só o próprio ponto) — solicitacoes_dp/views.py:marcacoes_do_dia."""
    from datetime import date

    from solicitacoes_dp import servicos
    try:
        dia = date.fromisoformat(request.query_params.get('data', ''))
    except ValueError:
        return Response({'horas': None})
    return Response({'horas': servicos.ponto_registrado(request.user, dia)})


@api_view(['GET', 'POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
@parser_classes([JSONParser, MultiPartParser, FormParser])
def solicitacoes(request):
    """GET: as minhas (solicitacoes_dp/views.py:minhas). POST: nova (views.nova → servicos.criar)."""
    from solicitacoes_dp import servicos
    S = _S()
    if request.method == 'POST':
        tipo = str(request.data.get('tipo') or '').upper()
        if tipo not in dict(S.TIPOS):
            return Response({'erro': 'Escolha o tipo de solicitação.', 'erros': ['Escolha o tipo de solicitação.']},
                            status=400)
        dados_form = servicos.ler(tipo, request.data)
        try:
            sol = servicos.criar(request.user, dados_form, request.FILES.getlist('anexos'))
        except servicos.Regra as erro:
            return _erro_regra(erro)
        return Response(_detalhe(request, _solicitacao_visivel(request, sol.pk)), status=201)

    lista = S.objects.filter(colaborador=request.user).prefetch_related('anexos')
    contagem = dict(lista.values_list('status').annotate(n=Count('id')).values_list('status', 'n'))
    status = request.query_params.get('status', '')
    if status == 'abertas':
        lista = lista.filter(status__in=S.ABERTOS)
    elif status in dict(S.STATUS):
        lista = lista.filter(status=status)
    itens, pagina = _pagina(request, lista)
    return Response({**pagina, 'resultados': [_resumo(request, s) for s in itens],
                     'contagem': {'abertas': sum(contagem.get(s, 0) for s in S.ABERTOS),
                                  'devolvidas': contagem.get(S.DEVOLVIDA, 0),
                                  'aprovadas': contagem.get(S.APROVADA, 0),
                                  'total': sum(contagem.values())}})


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
def solicitacao(request, pk):
    return Response(_detalhe(request, _solicitacao_visivel(request, pk)))


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
@parser_classes([JSONParser, MultiPartParser, FormParser])
def corrigir(request, pk):
    """O dono corrige e reenvia (solicitacoes_dp/views.py:corrigir → servicos.corrigir)."""
    from solicitacoes_dp import servicos
    S = _S()
    sol = get_object_or_404(S, pk=pk, colaborador=request.user)
    if sol.status not in (S.DEVOLVIDA, S.ENVIADA):
        return Response({'erro': 'Esta solicitação não pode mais ser corrigida.',
                         'erros': ['Esta solicitação não pode mais ser corrigida.']}, status=400)
    dados_form = servicos.ler(sol.tipo, request.data)
    dados_form['comentario'] = request.data.get('comentario', '') or ''
    remover = (request.data.getlist('remover') if hasattr(request.data, 'getlist')
               else (request.data.get('remover') or []))
    try:
        servicos.corrigir(sol, request.user, dados_form, request.FILES.getlist('anexos'), remover)
    except servicos.Regra as erro:
        return _erro_regra(erro)
    return Response(_detalhe(request, _solicitacao_visivel(request, sol.pk)))


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
def acao(request, pk):
    """As ações do detalhe (solicitacoes_dp/views.py:acao): cada uma confere a regra dentro de servicos."""
    from solicitacoes_dp import servicos
    sol = _solicitacao_visivel(request, pk)
    qual = request.data.get('acao')
    texto = request.data.get('texto', '') or ''
    try:
        if qual == 'cancelar':
            servicos.cancelar(sol, request.user, texto)
        elif qual == 'assumir':
            servicos.assumir(sol, request.user)
        elif qual == 'aprovar':
            servicos.aprovar(sol, request.user, texto, arquivar=_sim(request.data.get('arquivar')),
                             ajustado=_sim(request.data.get('ajustado')))
        elif qual == 'devolver':
            servicos.devolver(sol, request.user, texto)
        elif qual == 'lancar':
            servicos.lancar_ajuste(sol, request.user)
        elif qual == 'comentar':
            servicos.comentar(sol, request.user, texto)
        else:
            return Response({'erro': 'Ação desconhecida.', 'erros': ['Ação desconhecida.']}, status=400)
    except servicos.Regra as erro:
        return _erro_regra(erro)
    return Response(_detalhe(request, _solicitacao_visivel(request, pk)))


CAIXA_FILTROS = ('pendentes', 'devolvidas', 'lancar', 'aprovadas', 'todas')


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
def caixa(request):
    """Caixa do DP (solicitacoes_dp/views.py:caixa, linha 196) — só para quem analisa (e_analista)."""
    from datetime import timedelta

    from solicitacoes_dp import servicos
    from solicitacoes_dp.permissoes import e_analista
    if not e_analista(request.user):
        raise PermissionDenied('A caixa do DP é para quem analisa as solicitações.')
    S = _S()
    base = S.objects.select_related('colaborador', 'colaborador__sector', 'responsavel', 'responsavel__sector')
    hoje = timezone.localdate()
    kpis = {
        'aguardando': base.filter(status=S.ENVIADA).count(),
        'em_analise': base.filter(status=S.EM_ANALISE).count(),
        'devolvidas': base.filter(status=S.DEVOLVIDA).count(),
        'lancar': base.filter(tipo=S.AJUSTE, status=S.APROVADA, ajustado_no_ponto=False).count(),
        'aprovadas_mes': base.filter(status=S.APROVADA, aprovada_em__date__gte=hoje.replace(day=1)).count(),
        'atrasadas': base.filter(status__in=(S.ENVIADA, S.EM_ANALISE),
                                 enviada_em__lt=timezone.now() - timedelta(days=2)).count(),
    }
    filtro = request.query_params.get('filtro', 'pendentes')
    lista = base
    if filtro == 'pendentes':
        lista = lista.filter(status__in=(S.ENVIADA, S.EM_ANALISE)).order_by('enviada_em')
    elif filtro == 'devolvidas':
        lista = lista.filter(status=S.DEVOLVIDA)
    elif filtro == 'lancar':
        lista = lista.filter(tipo=S.AJUSTE, status=S.APROVADA, ajustado_no_ponto=False).order_by('aprovada_em')
    elif filtro == 'aprovadas':
        lista = lista.filter(status=S.APROVADA).order_by('-aprovada_em')
    tipo = request.query_params.get('tipo', '')
    if tipo in dict(S.TIPOS):
        lista = lista.filter(tipo=tipo)
    q = (request.query_params.get('q') or '').strip()
    if q:
        filtro_q = (Q(colaborador__first_name__icontains=q) | Q(colaborador__last_name__icontains=q)
                    | Q(colaborador__email__icontains=q))
        if q.lstrip('#').isdigit():
            filtro_q |= Q(pk=int(q.lstrip('#')))
        lista = lista.filter(filtro_q)
    de, ate = servicos._data(request.query_params.get('de')), servicos._data(request.query_params.get('ate'))
    if de:
        lista = lista.filter(data__gte=de)
    if ate:
        lista = lista.filter(data__lte=ate)
    itens, pagina = _pagina(request, lista.prefetch_related('anexos'))
    return Response({**pagina, 'kpis': kpis, 'filtro': filtro,
                     'resultados': [_resumo(request, s, com_colaborador=True) for s in itens]})


urlpatterns = [
    path('resumo/', resumo, name='ponto_resumo'),
    path('marcacoes/', relogio.marcacoes, name='ponto_marcacoes'),
    path('ajustes-solides/<int:pk>/cancelar/', relogio.ajuste_solides_cancelar, name='ponto_ajuste_cancelar'),
    path('ferias/', relogio.ferias, name='ponto_ferias'),
    path('escala/', relogio.escala, name='ponto_escala'),
    path('formulario/', formulario, name='ponto_formulario'),
    path('marcacoes-do-dia/', marcacoes_do_dia, name='ponto_marcacoes_do_dia'),
    path('solicitacoes/', solicitacoes, name='ponto_solicitacoes'),
    path('solicitacoes/<int:pk>/', solicitacao, name='ponto_solicitacao'),
    path('solicitacoes/<int:pk>/corrigir/', corrigir, name='ponto_corrigir'),
    path('solicitacoes/<int:pk>/acao/', acao, name='ponto_acao'),
    path('caixa/', caixa, name='ponto_caixa'),
]
