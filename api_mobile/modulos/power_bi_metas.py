"""Módulo Metas no app — o /power-bi/metas/ do portal no celular.

Regras (de onde veio cada uma):

- entrar: a view web ``goals_list_view`` (power_bi/views.py:916) só pede login
  e o item "Metas" do menu (templates/base.html:498) aparece para todo mundo
  logado — aqui também basta estar logado (o bloqueio do portal continua
  valendo pelo ``permissao('power_bi_metas')``);
- recorte e números: ``contexto`` abaixo é o ``goals_list_view``
  (power_bi/views.py:917-1182) linha a linha, com os helpers do próprio app
  (``_is_standard_user``, ``_is_gerentes_group_user``, ``_get_user_primary_store``,
  ``_stores_match``, ``_normalize_text``…). A view monta o contexto inline e
  devolve HTML, então a condição foi copiada citando a linha; o teste compara
  com o contexto da view web para as mesmas pessoas:
    * consultor (PADRAO fora do grupo GERENTES): só as linhas METAS CN REAL
      com o nome dele e a meta PDV da loja dele (setor/PDV), sem filtros;
    * gerente (PADRAO no grupo GERENTES): a loja dele, com filtro por CN;
    * demais hierarquias: a rede inteira, com filtro por loja, pilar e vendedor;
- histórico de cargas (arquivo, itens, atualização): só SUPERADMIN, como a
  ``manage_goals_view`` (power_bi/views.py:1187). Subir, excluir e sincronizar
  com o MySQL ficam no portal.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count
from django.urls import path
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from power_bi import views as pv
from power_bi.models import GoalEntry, GoalUpload

from .. import dados
from ..autenticacao import TokenMobileAuthentication
from . import permissao

ZERO = Decimal('0.00')


def pode_entrar(user):
    # power_bi/views.py:916 (@login_required) e templates/base.html:498 (item do menu sem condição).
    return bool(user and user.is_authenticated and user.is_active)


MODULO = {'rotulo': 'Metas', 'descricao': 'Metas do mês por pilar, CN e loja', 'icone': 'flag-outline',
          'cor': '#0EA5E9', 'pode_entrar': pode_entrar}

# power_bi/views.py:1061 — colunas da tabela do gerente.
COLUNAS_GERENTE = ['SVA', 'SEGURO', 'MOVEL', 'FIXA', 'SMARTPHONE', 'ELETRONICOS', 'ESSENCIAIS']


def _so(itens, cond):
    return [e for e in itens if cond(e)]


def contexto(user, params):
    """O ``goals_list_view`` (power_bi/views.py:917-1182) sem o render."""
    is_standard_user = pv._is_standard_user(user)
    is_gerente_user = is_standard_user and pv._is_gerentes_group_user(user)
    is_cn_user = is_standard_user and not is_gerente_user

    selected_year = params.get('year')
    selected_month = params.get('month')

    uploads = GoalUpload.objects.order_by('-year', '-month', '-updated_at')
    current_upload = None
    if selected_year and selected_month:
        try:
            current_upload = uploads.filter(year=int(selected_year), month=int(selected_month)).first()
        except (TypeError, ValueError):
            current_upload = None
    if current_upload is None:
        current_upload = uploads.first()

    entries = []
    selected_store = '' if (is_standard_user or is_gerente_user) else (params.get('store') or '').strip()
    selected_pilar = '' if (is_standard_user or is_gerente_user) else (params.get('pilar') or '').strip()
    selected_cn = (params.get('cn') or '').strip() if is_gerente_user else ''
    selected_seller = (params.get('seller') or '').strip() if not is_cn_user else ''

    if current_upload:
        entries = list(GoalEntry.objects.filter(upload=current_upload)
                       .order_by('sheet_type', 'store_name', 'pilar', 'user_name'))
        if is_cn_user:
            # views.py:943-958 — só as linhas CN com o nome da pessoa.
            entries = _so(entries, lambda e: e.sheet_type == GoalEntry.SHEET_CN_REAL)
            tokens = {
                pv._normalize_text(user.full_name), pv._normalize_text(user.get_full_name()),
                pv._normalize_text(user.first_name), pv._normalize_text(user.last_name),
                pv._normalize_text(user.username),
            }
            tokens = {t for t in tokens if t}
            entries = _so(entries, lambda e: pv._normalize_text(e.user_name) and pv._normalize_text(e.user_name) in tokens)
        elif is_gerente_user:
            # views.py:959-966 — a loja do gerente.
            loja = pv._get_user_primary_store(user)
            entries = _so(entries, lambda e: loja and pv._stores_match(pv._normalize_text(e.store_name), loja))
        else:
            # views.py:967-982 — a rede, com filtro de loja e pilar.
            if selected_store:
                alvo = pv._normalize_text(selected_store)
                entries = _so(entries, lambda e: pv._normalize_text(e.store_name) == alvo)
            if selected_pilar:
                alvo = pv._normalize_text(selected_pilar)
                entries = _so(entries, lambda e: pv._normalize_text(e.pilar) == alvo)

    fixa = bool(getattr(current_upload, 'fixa_as_percentage', False)) if current_upload else False
    cn_entries = _so(entries, lambda e: e.sheet_type == GoalEntry.SHEET_CN_REAL)

    # views.py:988-1004 — meta da loja.
    if is_cn_user and current_upload:
        loja = pv._get_user_primary_store(user)
        pdv_entries = [
            e for e in GoalEntry.objects.filter(upload=current_upload, sheet_type=GoalEntry.SHEET_PDV_REAL)
            .order_by('sheet_type', 'store_name', 'pilar', 'user_name')
            if loja and pv._stores_match(pv._normalize_text(e.store_name), loja)
        ]
    elif is_gerente_user or not is_standard_user:
        pdv_entries = _so(entries, lambda e: e.sheet_type == GoalEntry.SHEET_PDV_REAL)
    else:
        pdv_entries = []

    # views.py:1006-1024 — CN da loja (gerente) / vendedor (rede).
    available_cns = []
    available_sellers = sorted({e.user_name for e in cn_entries if e.user_name})
    if is_gerente_user:
        available_cns = available_sellers
        if selected_cn:
            alvo = pv._normalize_text(selected_cn)
            cn_entries = _so(cn_entries, lambda e: pv._normalize_text(e.user_name) == alvo)
    elif selected_seller:
        alvo = pv._normalize_text(selected_seller)
        cn_entries = _so(cn_entries, lambda e: pv._normalize_text(e.user_name) == alvo)

    all_stores, all_pilares = [], []
    if current_upload and not (is_standard_user or is_gerente_user):
        todas = list(GoalEntry.objects.filter(upload=current_upload).only('store_name', 'pilar'))
        all_stores = sorted({e.store_name for e in todas if e.store_name and not pv._is_network_store(e.store_name)})
        all_pilares = sorted({e.pilar for e in todas if e.pilar})

    def soma(itens):
        return sum((e.goal_value or ZERO) for e in itens if not (fixa and pv._is_fixa_pilar(e.pilar)))

    cn_total = soma(cn_entries)
    pdv_total = soma(pdv_entries)
    # views.py:1043-1048 — com vendedor escolhido o total é só o dele; senão o PDV evita somar duas vezes.
    if selected_cn or selected_seller:
        total = cn_total
    else:
        total = pdv_total if pdv_total > ZERO else cn_total

    def por_pilar(itens):
        mapa = defaultdict(lambda: ZERO)
        for e in itens:
            if e.goal_value is not None:
                mapa[e.pilar or 'SEM PILAR'] += e.goal_value
        maior = max(mapa.values()) if mapa else ZERO
        maior = maior if maior > ZERO else Decimal('1.00')
        return [{'pilar': p, 'total': float(mapa[p]), 'quantidade': fixa and pv._is_fixa_pilar(p),
                 'barra': float((mapa[p] / maior) * Decimal('100'))} for p in sorted(mapa.keys())]

    # views.py:1070-1092 — tabela CN × pilar do gerente.
    tabela = None
    if is_gerente_user:
        por_vendedor = defaultdict(lambda: defaultdict(lambda: ZERO))
        for e in cn_entries:
            if e.goal_value is None:
                continue
            por_vendedor[e.user_name or 'SEM CONSULTOR'][pv._normalize_text(e.pilar)] += e.goal_value
        linhas = []
        for vendedor in sorted(por_vendedor.keys()):
            total_linha = ZERO
            celulas = []
            for coluna in COLUNAS_GERENTE:
                valor = por_vendedor[vendedor].get(coluna, ZERO)
                qtd = fixa and pv._is_fixa_pilar(coluna)
                celulas.append({'valor': float(valor), 'quantidade': qtd})
                if not qtd:
                    total_linha += valor
            linhas.append({'vendedor': vendedor, 'celulas': celulas, 'total': float(total_linha)})
        tabela = {'colunas': COLUNAS_GERENTE, 'linhas': linhas}

    # views.py:1107-1120 — top consultores.
    por_consultor = defaultdict(lambda: ZERO)
    for e in cn_entries:
        if e.goal_value is None or (fixa and pv._is_fixa_pilar(e.pilar)):
            continue
        por_consultor[e.user_name or 'SEM CONSULTOR'] += e.goal_value
    top_consultores = sorted([{'nome': k, 'total': v} for k, v in por_consultor.items()],
                             key=lambda x: x['total'], reverse=True)[:10]

    # views.py:1122-1137 — top lojas.
    por_loja = defaultdict(lambda: ZERO)
    if is_gerente_user or not is_standard_user:
        for e in pdv_entries:
            if e.goal_value is None or (fixa and pv._is_fixa_pilar(e.pilar)):
                continue
            por_loja[e.store_name or 'SEM LOJA'] += e.goal_value
    top_lojas = sorted([{'nome': k, 'total': v} for k, v in por_loja.items()],
                       key=lambda x: x['total'], reverse=True)[:10]

    return {
        'uploads': list(uploads[:24]),
        'current_upload': current_upload,
        'is_standard_user': is_standard_user,
        'is_gerente_user': is_gerente_user,
        'is_cn_user': is_cn_user,
        'selected_store': selected_store,
        'selected_pilar': selected_pilar,
        'selected_cn': selected_cn,
        'selected_seller': selected_seller,
        'all_stores': all_stores,
        'all_pilares': all_pilares,
        'available_cns': available_cns,
        'available_sellers': available_sellers,
        'total_value': total,
        'cn_total': cn_total,
        'pdv_total': pdv_total,
        'cn_entries_count': len(cn_entries),
        'pdv_entries_count': len(pdv_entries),
        'cn_pilar_rows': por_pilar(cn_entries),
        'pdv_pilar_rows': por_pilar(pdv_entries),
        'manager_table': tabela,
        'top_consultants': top_consultores,
        'top_stores': top_lojas,
        'fixa_as_percentage': fixa,
    }


def _rotulo(u):
    return f'{u.month:02d}/{u.year}'


def _competencia(u):
    return {'ano': u.year, 'mes': u.month, 'rotulo': _rotulo(u), 'fixa_quantidade': bool(u.fixa_as_percentage),
            'atualizado_em': dados.iso(u.updated_at)}


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('power_bi_metas')])
def resumo(request):
    q = request.query_params
    # Nomes do app → nomes do formulário da web (templates/power_bi/goals_list.html).
    ctx = contexto(request.user, {'year': q.get('ano'), 'month': q.get('mes'), 'store': q.get('loja'),
                                  'pilar': q.get('pilar'), 'seller': q.get('vendedor'), 'cn': q.get('cn')})
    u = request.user
    perfil = 'cn' if ctx['is_cn_user'] else 'gerente' if ctx['is_gerente_user'] else 'rede'
    atual = ctx['current_upload']
    try:
        pedido = (int(q['ano']), int(q['mes'])) if q.get('ano') and q.get('mes') else None
    except (TypeError, ValueError):
        pedido = (0, 0)
    # templates/power_bi/goals_list.html — o que cada perfil vê.
    return Response({
        'perfil': perfil,
        'pode_gerenciar': pv._is_superadmin(u),
        'competencia': _competencia(atual) if atual else None,
        # pediu um mês sem carga: a web cai na mais recente; o app avisa.
        'competencia_trocada': bool(atual and pedido and (atual.year, atual.month) != pedido),
        'competencias': [_competencia(x) for x in ctx['uploads']],
        'filtros': {
            # rede: loja, pilar e vendedor; gerente: CN da loja; consultor: nenhum.
            'lojas': ctx['all_stores'],
            'pilares': ctx['all_pilares'],
            'vendedores': ctx['available_sellers'] if perfil == 'rede' else [],
            'cns': ctx['available_cns'],
            'loja': ctx['selected_store'],
            'pilar': ctx['selected_pilar'],
            'vendedor': ctx['selected_seller'] if perfil == 'rede' else '',
            'cn': ctx['selected_cn'],
        },
        'fixa_quantidade': ctx['fixa_as_percentage'],
        'totais': {
            'meta': None if ctx['is_gerente_user'] else float(ctx['total_value']),
            'cn': float(ctx['cn_total']),
            'loja': float(ctx['pdv_total']),
        },
        'contagens': {'cn': ctx['cn_entries_count'], 'loja': ctx['pdv_entries_count']},
        'painel_cn': ctx['cn_pilar_rows'],
        'tabela_gerente': ctx['manager_table'],
        'top_consultores': {
            'titulo': 'Meu resumo CN' if ctx['is_standard_user'] else 'Top consultores (CN)',
            'itens': [{'nome': x['nome'], 'total': float(x['total'])} for x in ctx['top_consultants']],
        } if (ctx['is_standard_user'] or u.is_superuser) else None,
        'painel_loja': ctx['pdv_pilar_rows'],
        'top_lojas': {
            'titulo': 'Minha loja' if ctx['is_cn_user'] else 'Top lojas (PDV)',
            'itens': [{'nome': x['nome'], 'total': float(x['total'])} for x in ctx['top_stores']],
        },
    })


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('power_bi_metas')])
def cargas(request):
    # power_bi/views.py:1187 — a lista de cargas é do painel de gestão, só SUPERADMIN.
    if not pv._is_superadmin(request.user):
        raise PermissionDenied('Apenas SUPERADMIN vê o histórico de cargas.')
    lista = GoalUpload.objects.select_related('uploaded_by').annotate(n=Count('entries')).order_by('-year', '-month', '-updated_at')
    return Response({'resultados': [{
        **_competencia(c),
        'id': c.id,
        'arquivo': c.source_file_name or '',
        'itens': c.n,
        'enviado_por': c.uploaded_by.get_full_name() if c.uploaded_by else None,
        'criado_em': dados.iso(c.created_at),
    } for c in lista]})


urlpatterns = [
    path('', resumo, name='power_bi_metas_resumo'),
    path('cargas/', cargas, name='power_bi_metas_cargas'),
]
