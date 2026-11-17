"""Módulo Simulador no app — o /simulator/ no celular ("meu simulador").

O cálculo é o motor do portal (``simulator/services.py``: ``compute_*_simulation``
dentro do ``realized_prefetch``, que guarda o realizado do mês por 15 min); aqui
só se resolve quem é calculado — com a mesma regra do ``simulator_dashboard``
(simulator/views.py:94-222) — e se monta o JSON que o app desenha.

Fica de fora: o modo "Simulador" com valores digitados, o "copiar valores" e as
telas de administração (/simulator/admin/...), que continuam no portal.
"""
import logging

from django.http import Http404
from django.urls import path
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from simulator import services as sv
from simulator.sql_realizado import realized_prefetch
from simulator.views import get_sector_consultors, get_sector_users
from users.models import User

from ..autenticacao import TokenMobileAuthentication
from . import permissao

logger = logging.getLogger(__name__)


def pode_entrar(user):
    # templates/base.html:539 (`{% if user.is_authenticated %}`) e @login_required em
    # simulator/views.py:94: todo mundo logado abre o simulador; o recorte é por papel.
    return bool(user and user.is_authenticated and user.is_active)


MODULO = {'rotulo': 'Simulador', 'descricao': 'Metas, realizado, projeção e prêmio estimado por pilar',
          'icone': 'calculator-outline', 'cor': '#84CC16', 'pode_entrar': pode_entrar}

PAPEIS = {
    sv.ROLE_SUPERADMIN: 'Superadmin', sv.ROLE_COORDENADOR: 'Coordenador', sv.ROLE_GERENTE: 'Gerente',
    sv.ROLE_CONSULTOR: 'Consultor', sv.ROLE_APART: 'A parte',
}
VISOES = {sv.VIEW_PROJECAO: 'Projeção', sv.VIEW_REALIZADO: 'Realizado'}


def _rotulo_papel(u):
    return PAPEIS.get(sv.get_user_role(u), '')


# ── Quem pode ser calculado (simulator/views.py:107-222) ─────────────────────

def alvos_disponiveis(user, papel):
    """A lista do seletor de pessoa, papel a papel, como o dashboard monta."""
    if papel == sv.ROLE_SUPERADMIN:
        coords = sv.get_all_coordinators()
        aparte = sv.get_all_aparte_users()
        ids_coord = {u.id for u in coords}
        ids_aparte = {u.id for u in aparte}
        itens = ([(u, 'A parte') for u in aparte]
                 + [(u, 'Coordenador') for u in coords if u.id not in ids_aparte]
                 + [(u, 'Sniper') for u in sv.get_all_snipers() if u.id not in ids_coord]
                 + [(u, 'Gerente') for u in sv.get_all_gerentes() if u.id not in ids_aparte]
                 + [(u, 'Consultor') for u in sv.get_all_consultors() if u.id not in ids_aparte])
        return [_item(u, r) for u, r in itens]
    if papel == sv.ROLE_COORDENADOR:
        setores = sv.get_coordinator_sectors(user)
        qs = (User.objects.filter(is_active=True, sector__in=setores)
              .exclude(id__in=sv.get_simulator_excluded_user_ids()).exclude(id=user.id)
              .select_related('sector').order_by('first_name', 'last_name'))
        return [_item(user, 'Coordenador')] + [_item(u, _rotulo_papel(u)) for u in qs]
    if papel == sv.ROLE_GERENTE:
        return [_item(u, _rotulo_papel(u)) for u in get_sector_users(user)]
    if papel == sv.ROLE_APART:
        return [_item(user, 'A parte')]
    return [_item(u, 'Consultor') for u in get_sector_consultors(user)]


def _item(u, papel_rotulo):
    return {'id': u.id, 'nome': u.get_full_name() or u.email,
            'setor': u.sector.name if u.sector_id else None, 'papel': papel_rotulo}


def resolver_alvo(user, papel, usuario_id):
    """``(alvo, papel_do_calculo)`` ou ``(None, None)`` (superadmin sem escolha).

    Mesmas checagens do dashboard; onde a web volta com "não pertence ao seu
    setor/às suas lojas", aqui é 404.
    """
    alvo = None
    if usuario_id:
        try:
            alvo = User.objects.select_related('sector').get(pk=int(usuario_id), is_active=True)
        except (TypeError, ValueError, User.DoesNotExist):
            raise Http404('Pessoa não encontrada.')

    if papel == sv.ROLE_SUPERADMIN:                                     # views.py:134-143
        if alvo is None:
            return None, None
        return alvo, (sv.ROLE_COORDENADOR if sv.is_sniper_user(alvo) else sv.get_user_role(alvo))
    if papel == sv.ROLE_COORDENADOR:                                    # views.py:174-183
        if alvo is None:
            return user, sv.ROLE_COORDENADOR
        if alvo.id != user.id and alvo.sector not in sv.get_coordinator_sectors(user):
            raise Http404('Pessoa não encontrada.')
        return alvo, sv.get_user_role(alvo)
    if papel == sv.ROLE_GERENTE:                                        # views.py:191-199
        if alvo is None:
            return user, sv.ROLE_GERENTE
        if alvo.sector != user.sector:
            raise Http404('Pessoa não encontrada.')
        return alvo, sv.get_user_role(alvo)
    if papel == sv.ROLE_APART:                                          # views.py:198-206 (só o próprio)
        if alvo is not None and alvo.id != user.id:
            raise Http404('Pessoa não encontrada.')
        return user, sv.ROLE_APART
    if alvo is None:                                                    # views.py:207-222 (consultor)
        return user, sv.ROLE_CONSULTOR
    if alvo.sector != user.sector:
        raise Http404('Pessoa não encontrada.')
    return alvo, sv.ROLE_CONSULTOR


# ── Cálculo (o fragmento de resultados do dashboard, views.py:262-300) ───────

def calcular(alvo, papel_alvo, visao, hunter, acelerador):
    with realized_prefetch():
        if papel_alvo == sv.ROLE_APART:
            from users.models import AParteCommissionConfig
            config = AParteCommissionConfig.objects.filter(user=alvo).first()
            return sv.compute_aparte_simulation(alvo, config, hunter, view_mode=visao, simulator_inputs={})
        fatores = sv.get_factor_set(papel_alvo).data
        if papel_alvo == sv.ROLE_CONSULTOR:
            return sv.compute_consultor_simulation(alvo, fatores, hunter, view_mode=visao, simulator_inputs={},
                                                   acelerador_microindicadores=acelerador)
        if papel_alvo == sv.ROLE_GERENTE:
            return sv.compute_gerente_simulation(alvo, fatores, hunter, view_mode=visao, simulator_inputs={})
        if papel_alvo == sv.ROLE_COORDENADOR:
            return sv.compute_coordenador_simulation(alvo, fatores, hunter, view_mode=visao, simulator_inputs={})
    return None


def _n(valor, casas=2):
    if valor is None:
        return None
    try:
        return round(float(valor), casas)
    except (TypeError, ValueError):
        return None


def serializar(sim, papel_alvo, dias_restantes):
    linhas = []
    for row in sim.get('rows') or []:
        meta = row.get('meta') or 0
        fixa = row.get('key') == 'fixa'
        realizado = (row.get('quantity') if fixa else row.get('proj')) or 0
        falta = meta - realizado
        # Meta-diária (views.py:288-294): o que falta por dia útil para 100%.
        por_dia = (falta / dias_restantes) if (dias_restantes > 0 and falta > 0) else 0
        # O dinheiro do pilar não mora no mesmo campo em todo papel (simulator/tabela.py:_pilares).
        ganho = max(float(row.get('total_with_pdv') or 0), float(row.get('total_individual') or 0),
                    float(row.get('commission_value') or 0))
        linhas.append({
            'chave': row.get('key'), 'rotulo': row.get('label'),
            'quantidade': fixa,
            'meta': _n(meta), 'realizado': _n(realizado), 'receita': _n(row.get('revenue')),
            'atingimento': _n(row.get('attainment'), 4),            # fração: 0,88 = 88%
            'por_dia': _n(por_dia),
            'taxa_comissao': _n(row.get('commission_rate'), 4),
            'comissao': _n(row.get('commission_value')),
            'taxa_premio': _n(row.get('premium_rate'), 4),
            'premio': _n(row.get('premium_value')),
            'premio_pdv': _n(row.get('pdv_premium_value')),
            'hunter2': _n(row.get('hunter2_value')), 'hunter3': _n(row.get('hunter3_value')),
            'ganho': _n(ganho),
            'loja': ({'meta': _n(row.get('pdv_meta')), 'realizado': _n(row.get('pdv_proj')),
                      'atingimento': _n(row.get('pdv_attainment'), 4)}
                     if papel_alvo == sv.ROLE_CONSULTOR and row.get('pdv_meta') is not None else None),
            'coordenacao': ({'meta': _n(row.get('coord_meta')), 'realizado': _n(row.get('coord_proj')),
                             'atingimento': _n(row.get('coord_attainment'), 4)}
                            if papel_alvo != sv.ROLE_CONSULTOR and row.get('coord_meta') is not None else None),
        })
    t = sim.get('totals') or {}
    if sim.get('is_sniper'):
        papel_rotulo = 'Sniper'
    elif sim.get('is_gerente_operacional'):
        papel_rotulo = 'Gerente operacional'
    else:
        papel_rotulo = PAPEIS.get(papel_alvo, '')
    return {
        'nome': sim.get('user_name') or '',
        'pdv': sim.get('pdv') or None,
        'coordenador': sim.get('coordinator') or None,
        'papel': papel_alvo, 'papel_rotulo': papel_rotulo,
        'ganho_total': _n(t.get('ganho_total')),
        'totais': {
            'pilares': _n(t.get('total_with_pdv') if 'total_with_pdv' in t else t.get('total_commission')),
            'hunter2': _n(t.get('hunter2')), 'hunter3': _n(t.get('hunter3')),
            'bonus_6_7': _n(t.get('bonus_6_7')),
            'acelerador': _n(t.get('acelerador_microindicadores')),
            'sem_acelerador': _n(t.get('ganho_total_sem_acelerador')),
        },
        'acelerador_ligado': bool(sim.get('acelerador_microindicadores')),
        'acelerador_pct': sim.get('acelerador_microindicadores_pct') or round(sv.ACELERADOR_MICROINDICADORES_RATE * 100),
        'sniper': bool(sim.get('is_sniper')),
        'sniper_de': sim.get('sniper_coordinator') or None,
        'gerente_operacional': bool(sim.get('is_gerente_operacional')),
        'percentual_operacional': sim.get('percentual_operacional_pct'),
        'gerente_base': sim.get('gerente_de_vendas_base') or None,
        'salario_base': _n(sim.get('base_salary')) if 'base_salary' in sim else None,
        'pilares': linhas,
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('simulador')])
def resumo(request):
    user = request.user
    papel = sv.get_user_role(user)
    visao = request.query_params.get('visao') or sv.VIEW_PROJECAO
    if visao not in VISOES:
        visao = sv.VIEW_PROJECAO
    alvo, papel_alvo = resolver_alvo(user, papel, (request.query_params.get('usuario') or '').strip())
    passados, total = sv.get_business_days_info()
    restantes = max(total - passados, 0)
    saida = {
        'papel': papel, 'papel_rotulo': PAPEIS.get(papel, ''),
        'pode_ver_outros': papel not in (sv.ROLE_APART,),
        'visao': visao, 'visoes': [{'valor': k, 'rotulo': r} for k, r in VISOES.items()],
        'dias_uteis': {'passados': passados, 'total': total, 'restantes': restantes},
        'precisa_escolher': alvo is None,
        'alvo': _item(alvo, PAPEIS.get(papel_alvo, '')) if alvo else None,
        'vendo_outro': bool(alvo and alvo.id != user.id),
        'simulacao': None, 'erro': None,
    }
    if alvo is None:
        return Response(saida)
    # Níveis de Hunter e o Acelerador B2B: opções da tela, como na web (?hunter_<pilar>=2&acelerador=1).
    hunter = sv.get_hunter_levels_from_request(request._request)
    acelerador = sv.get_acelerador_from_request(request._request)
    try:
        sim = calcular(alvo, papel_alvo, visao, hunter, acelerador)
    except Exception:                                   # noqa: BLE001 — MySQL/planilha fora do ar
        logger.exception('Simulador (app): falha ao calcular %s (%s)', alvo.pk, papel_alvo)
        saida['erro'] = 'Não foi possível calcular agora. Tente de novo em instantes.'
        return Response(saida)
    if not sim:
        saida['erro'] = 'Nenhuma simulação disponível para esta pessoa.'
    elif sim.get('error'):
        saida['erro'] = sim['error']
    else:
        saida['simulacao'] = serializar(sim, papel_alvo, restantes)
    return Response(saida)


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('simulador')])
def pessoas(request):
    user = request.user
    return Response({'resultados': alvos_disponiveis(user, sv.get_user_role(user))})


urlpatterns = [
    path('', resumo, name='simulador_resumo'),
    path('pessoas/', pessoas, name='simulador_pessoas'),
]
