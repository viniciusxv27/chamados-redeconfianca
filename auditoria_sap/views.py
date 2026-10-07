"""Telas da Visão SAP: a lista da auditoria e o painel administrativo."""
import logging
from datetime import datetime
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Abs, Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .espelho import sincronizar, ultima_sincronizacao
from .models import (LinhaAuditoria, filtro_aguardando_leitura, filtro_resolvidas_pendentes,
                     inicio_da_ultima_leitura)
from .mysql import SapIndisponivel
from .permissions import e_gestor as _e_gestor
from .permissions import lojas_visiveis, normalizar_loja
from .permissions import pode_ver as _pode_ver

logger = logging.getLogger(__name__)
ZERO = Decimal('0.00')
POR_PAGINA = 50

SITUACOES = (('abertas', 'Só as abertas'), ('pendentes', 'Resolvidas, mas ainda pendentes'),
             ('aguardando', 'Resolvidas, aguardando a próxima leitura'), ('resolvidas', 'Só as resolvidas'),
             ('todas', 'Todas'))
PRESENCAS = (('na', 'Ainda na auditoria'), ('sairam', 'Já saíram do SAP'), ('todas', 'Todas'))
ORDENS = (
    ('recente', 'Venda mais recente'),
    ('antiga', 'Venda mais antiga'),
    ('diferenca', 'Maior diferença de valor'),
    ('loja', 'Loja'),
    ('tipo', 'Tipo do erro'),
)
COLUNAS_DE_BUSCA = ('id_venda', 'nome_cliente', 'documento_vivogo', 'serial_vivogo',
                    'serial_sap', 'produto_vivogo', 'produto_sap', 'num_fat_sap',
                    'sku_vivogo', 'ordem_sap', 'pdv')
COMPARACOES = (('documento', 'status_documento'), ('produto', 'status_produto'),
               ('valor', 'status_valor'), ('loja', 'status_loja'), ('data', 'status_data'))


def so_auditoria(view):
    """Trava de tela: quem não pode ver a auditoria não entra nem pela URL."""
    @wraps(view)
    def _view(request, *args, **kwargs):
        if not _pode_ver(request.user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'erro': 'Sem acesso à Visão SAP.'}, status=403)
            messages.error(request, 'A Visão SAP é restrita à administração.')
            return redirect('home')
        return view(request, *args, **kwargs)
    return _view


def pdvs_visiveis(user):
    """None quando a pessoa vê a rede; senão os PDVs do espelho que são dela.

    O SAP escreve a loja do seu jeito ("GLÓRIA", "ITACIBA") e o cadastro do
    portal do dele ("Loja Glória", "Loja Itacibá"): a comparação é sem "Loja",
    sem acento e em maiúsculas. Loja sem nenhuma linha no espelho dá lista
    vazia — e a tela fica vazia, não aberta.
    """
    lojas = lojas_visiveis(user)
    if lojas is None:
        return None
    todos = LinhaAuditoria.objects.exclude(pdv='').values_list('pdv', flat=True).distinct()
    return sorted(p for p in todos if normalizar_loja(p) in lojas)


def _data(texto):
    try:
        return datetime.strptime((texto or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def ler_filtros(request):
    """O que a tela está filtrando — os mesmos filtros na lista e no painel."""
    de, ate = _data(request.GET.get('de')), _data(request.GET.get('ate'))
    if de and ate and de > ate:
        de, ate = ate, de
    situacao = request.GET.get('situacao') or 'abertas'
    if situacao not in dict(SITUACOES):
        situacao = 'abertas'
    presenca = request.GET.get('presenca') or 'na'
    if presenca not in dict(PRESENCAS):
        presenca = 'na'
    ordem = request.GET.get('ordem') or 'recente'
    if ordem not in dict(ORDENS):
        ordem = 'recente'
    comparacao = request.GET.get('comparacao') or ''
    if comparacao not in dict(COMPARACOES):
        comparacao = ''
    return {
        'tipo': (request.GET.get('tipo') or '').strip(),
        'loja': (request.GET.get('loja') or '').strip(),
        'de': de, 'ate': ate,
        'situacao': situacao,
        'presenca': presenca,
        'comparacao': comparacao,
        'q': (request.GET.get('q') or '').strip(),
        'ordem': ordem,
    }


def filtrar(filtros, corte=None, pdvs=None):
    """A consulta do espelho já com os filtros da tela (e só as lojas de quem vê)."""
    qs = LinhaAuditoria.objects.all()
    if pdvs is not None:
        qs = qs.filter(pdv__in=pdvs)
    if filtros['presenca'] == 'na':
        qs = qs.filter(ativa=True)
    elif filtros['presenca'] == 'sairam':
        qs = qs.filter(ativa=False)
    if filtros['situacao'] == 'abertas':
        qs = qs.filter(resolvida=False)
    elif filtros['situacao'] == 'resolvidas':
        qs = qs.filter(resolvida=True)
    elif filtros['situacao'] == 'pendentes':
        qs = qs.filter(filtro_resolvidas_pendentes(corte if corte is not None else inicio_da_ultima_leitura()))
    elif filtros['situacao'] == 'aguardando':
        qs = qs.filter(filtro_aguardando_leitura(corte if corte is not None else inicio_da_ultima_leitura()))
    if filtros['tipo']:
        qs = qs.filter(tipo_erro=filtros['tipo'])
    if filtros['loja']:
        qs = qs.filter(pdv=filtros['loja'])
    if filtros['de']:
        qs = qs.filter(data_venda__gte=filtros['de'])
    if filtros['ate']:
        qs = qs.filter(data_venda__lte=filtros['ate'])
    if filtros['comparacao']:
        campo = dict(COMPARACOES)[filtros['comparacao']]
        # "Divergente" é o que interessa; OK e "não comparado" ficam de fora.
        qs = qs.filter(**{f'{campo}__icontains': 'DIVERG'})
    if filtros['q']:
        procura = Q()
        for coluna in COLUNAS_DE_BUSCA:
            procura |= Q(**{f'{coluna}__icontains': filtros['q']})
        qs = qs.filter(procura)
    return qs


def ordenar(qs, ordem):
    if ordem == 'antiga':
        return qs.order_by('data_venda', 'pdv', 'id_venda')
    if ordem == 'diferenca':
        return (qs.annotate(peso=Abs(Coalesce('diferenca_valor', Value(ZERO))))
                  .order_by('-peso', '-data_venda'))
    if ordem == 'loja':
        return qs.order_by('pdv', '-data_venda', 'id_venda')
    if ordem == 'tipo':
        return qs.order_by('tipo_erro', '-data_venda', 'id_venda')
    return qs.order_by('-data_venda', 'pdv', 'id_venda')


def _resumo(qs):
    """Os números do topo, numa consulta só."""
    dados = qs.aggregate(
        total=Count('id'),
        resolvidas=Count('id', filter=Q(resolvida=True)),
        em_risco=Coalesce(Sum(Abs(Coalesce('diferenca_valor', Value(ZERO)))),
                          Value(ZERO), output_field=DecimalField(max_digits=16, decimal_places=2)),
    )
    dados['abertas'] = dados['total'] - dados['resolvidas']
    dados['percentual'] = round(dados['resolvidas'] * 100 / dados['total']) if dados['total'] else 0
    return dados


def _contexto_comum(request, filtros, pdvs=None):
    """O que lista e painel mostram igual: filtros, listas de escolha, espelho."""
    visiveis = LinhaAuditoria.objects.filter(ativa=True)
    if pdvs is not None:
        visiveis = visiveis.filter(pdv__in=pdvs)
    return {
        'filtros': filtros,
        'tipos': (visiveis.exclude(tipo_erro='').values_list('tipo_erro', flat=True)
                  .distinct().order_by('tipo_erro')),
        'lojas': (visiveis.exclude(pdv='').values_list('pdv', flat=True).distinct().order_by('pdv')),
        # O gerente vê só a loja dele: a tela diz isso em vez de oferecer um
        # filtro de loja com uma opção só.
        'so_da_loja': pdvs is not None,
        'loja_do_usuario': (request.user.sector.name if pdvs is not None and request.user.sector_id
                            else ''),
        'situacoes': SITUACOES,
        'presencas': PRESENCAS,
        'ordens': ORDENS,
        'comparacoes': COMPARACOES,
        'ultima': ultima_sincronizacao(),
        'e_gestor': _e_gestor(request.user),
        'tem_espelho': LinhaAuditoria.objects.exists(),
    }


@login_required
@so_auditoria
def lista(request):
    """A auditoria linha a linha, com filtro, ordem e o botão de resolver."""
    filtros = ler_filtros(request)
    pdvs = pdvs_visiveis(request.user)
    qs = ordenar(filtrar(filtros, pdvs=pdvs), filtros['ordem'])
    resumo = _resumo(qs)

    pagina = Paginator(qs.select_related('resolvida_por'), POR_PAGINA).get_page(request.GET.get('pagina'))
    corte = inicio_da_ultima_leitura()
    for linha in pagina:
        linha.estado = linha.estado_da_resolucao(corte)

    # A paginação precisa manter os filtros e trocar só a página.
    parametros = request.GET.copy()
    parametros.pop('pagina', None)

    contexto = _contexto_comum(request, filtros, pdvs)
    contexto.update({
        'pagina': pagina,
        'resumo': resumo,
        'aba': 'lista',
        'query': parametros.urlencode(),
    })
    return render(request, 'auditoria_sap/lista.html', contexto)


@login_required
@so_auditoria
def painel(request):
    """O painel: pendências por loja e o que foi dado como resolvido sem sair do SAP.

    O ciclo é: o gerente marca "resolvido"; na leitura seguinte do SAP, se a linha
    sumiu, ela sai daqui (corrigida); se continua lá, ela entra no quadro
    "Resolvidas, mas ainda pendentes" — alguém disse que resolveu e não resolveu.
    """
    filtros = ler_filtros(request)
    pdvs = pdvs_visiveis(request.user)
    corte = inicio_da_ultima_leitura()
    # O painel conta todas as situações; o filtro de situação vale só para a lista.
    base = filtrar({**filtros, 'situacao': 'todas', 'presenca': 'na'}, corte, pdvs)

    q_abertas = Q(resolvida=False)
    q_pendentes = filtro_resolvidas_pendentes(corte)
    q_aguardando = filtro_aguardando_leitura(corte)
    numeros = base.aggregate(abertas=Count('id', filter=q_abertas),
                             pendentes=Count('id', filter=q_pendentes),
                             aguardando=Count('id', filter=q_aguardando))
    corrigidas = filtrar({**filtros, 'situacao': 'todas', 'presenca': 'sairam'}, corte, pdvs).count()

    por_loja = list(base.values('pdv')
                    .annotate(abertas=Count('id', filter=q_abertas), pendentes=Count('id', filter=q_pendentes),
                              aguardando=Count('id', filter=q_aguardando))
                    .filter(Q(abertas__gt=0) | Q(pendentes__gt=0) | Q(aguardando__gt=0))
                    .order_by('-pendentes', '-abertas', 'pdv'))
    for loja in por_loja:
        loja['total'] = loja['abertas'] + loja['pendentes']
    maior = max((l['total'] for l in por_loja), default=0)

    resolvidas_pendentes = list(base.filter(q_pendentes).select_related('resolvida_por')
                                .order_by('pdv', '-resolvida_em')[:100])

    parametros = request.GET.copy()
    for chave in ('situacao', 'presenca', 'loja', 'pagina'):
        parametros.pop(chave, None)

    contexto = _contexto_comum(request, filtros, pdvs)
    contexto.update({
        'numeros': numeros,
        'corrigidas': corrigidas,
        'por_loja': por_loja,
        'maior_loja': maior,
        'resolvidas_pendentes': resolvidas_pendentes,
        'corte': corte,
        'aba': 'painel',
        # Para os links de detalhe: os filtros do painel, sem situação/loja (cada link põe a sua).
        'query': parametros.urlencode(),
    })
    return render(request, 'auditoria_sap/painel.html', contexto)


def _linha_visivel(user, linha_id):
    """A linha, se ela for de uma loja que a pessoa vê — senão 404, nem pela URL."""
    pdvs = pdvs_visiveis(user)
    qs = LinhaAuditoria.objects.all() if pdvs is None else LinhaAuditoria.objects.filter(pdv__in=pdvs)
    return get_object_or_404(qs, id=linha_id)


@login_required
@so_auditoria
def detalhe(request, linha_id):
    """A linha inteira, como veio do SAP, mais o histórico de quem marcou."""
    linha = _linha_visivel(request.user, linha_id)
    historico = [{
        'quem': m.usuario.full_name if m.usuario else 'Usuário removido',
        'resolvida': m.resolvida,
        'quando': timezone.localtime(m.quando).strftime('%d/%m/%Y %H:%M'),
        'observacao': m.observacao,
    } for m in linha.marcacoes.select_related('usuario')[:20]]
    return JsonResponse({
        'ok': True,
        'campos': [{'coluna': c, 'valor': v} for c, v in (linha.dados or {}).items()],
        'historico': historico,
        'resolvida': linha.resolvida,
        'observacao': linha.observacao,
        'ativa': linha.ativa,
    })


@login_required
@so_auditoria
def marcar(request, linha_id):
    """Marca (ou desmarca) a linha como resolvida, guardando quem foi."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Use POST.'}, status=405)
    linha = _linha_visivel(request.user, linha_id)
    resolvida = (request.POST.get('resolvida') or '').lower() in ('1', 'true', 'sim', 'on')
    marcacao = linha.marcar(request.user, resolvida, request.POST.get('observacao') or '')
    return JsonResponse({
        'ok': True,
        'resolvida': linha.resolvida,
        'quem': marcacao.usuario.full_name if marcacao.usuario else '',
        'quando': timezone.localtime(marcacao.quando).strftime('%d/%m/%Y %H:%M'),
        'observacao': linha.observacao,
    })


@login_required
@so_auditoria
def atualizar(request):
    """Relê a view do SAP. Demora ~30 s: é ação de botão, não de abrir tela."""
    if request.method != 'POST':
        return redirect('auditoria_sap:lista')
    if not _e_gestor(request.user):
        messages.error(request, 'Só a administração atualiza a auditoria do SAP.')
        return redirect('auditoria_sap:lista')
    try:
        resumo = sincronizar(por=request.user)
    except SapIndisponivel as exc:
        messages.error(request, f'Não deu para ler o SAP agora: {exc}')
        return redirect(request.POST.get('voltar') or 'auditoria_sap:lista')
    messages.success(request, (
        f"Auditoria atualizada: {resumo['total']} linhas — {resumo['novas']} novas, "
        f"{resumo['atualizadas']} alteradas e {resumo['sumiram']} que saíram "
        f"({resumo['segundos']:.0f} s)."))
    return redirect(request.POST.get('voltar') or 'auditoria_sap:lista')
