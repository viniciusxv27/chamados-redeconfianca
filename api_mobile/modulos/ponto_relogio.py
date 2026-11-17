"""Módulo Ponto no app — parte do relógio (Tangerino/Sólides): marcações, férias e escala.

Só LEITURA. Nada aqui bate ponto nem escreve na Sólides: o registro de ponto
(``tangerino.client.registrar_ponto``) e o ajuste aprovado (``tangerino.ajustes.aplicar``)
ficam de fora do app de propósito. O único ato daqui é cancelar o próprio pedido de
ajuste que ainda aguarda — que só muda o status no banco do portal.

Cada seção pergunta a mesma regra da tela web:
- Meu ponto: ``ConfiguracaoTangerino.libera_ponto`` (tangerino/views.py:58, ``modulo_liberado``).
- Férias: ``ConfiguracaoTangerino.libera_ferias`` (tangerino/views.py:78, ``ferias_liberadas``).
- Escala: só login (tangerino/views.py:683); aqui, só a escala da própria pessoa
  (o ramo "colaborador" da view, tangerino/views.py:731).
"""
from datetime import date, timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .. import dados
from ..autenticacao import TokenMobileAuthentication
from . import permissao

DIAS_PT = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']


def _config():
    from tangerino.models import ConfiguracaoTangerino
    return ConfiguracaoTangerino.get()


def ponto_liberado(user, config=None):
    return bool((config or _config()).libera_ponto(user))


def ferias_liberadas(user, config=None):
    return bool((config or _config()).libera_ferias(user))


def _hhmm(dt):
    if not dt:
        return None
    return (timezone.localtime(dt) if timezone.is_aware(dt) else dt).strftime('%H:%M')


def _evento(e):
    return {'tipo': e['tipo'], 'hora': _hhmm(e['quando']), 'iso': dados.iso(e['quando']),
            'editado': bool(e.get('editado'))}


def _dia(d):
    return {
        'dia': d['dia'].isoformat(),
        'semana': DIAS_PT[d['dia'].weekday()],
        'eventos': [_evento(e) for e in d['eventos']],
        'horas': d['horas'],
        'segundos': d['segundos'],
        'previsto': d['previsto'],
        'previsto_segundos': d['previsto_segundos'],
        'diferenca_segundos': d['diferenca'],
        'aberto': d['aberto'],
        'fim_de_semana': d['fim_de_semana'],
        'sem_marcacao': d['sem_marcacao'],
    }


def _totais(ctx, rotulo):
    return {'feito': ctx.get(f'feito_{rotulo}'), 'previsto': ctx.get(f'previsto_{rotulo}'),
            'diferenca': ctx.get(f'diferenca_{rotulo}'),
            'diferenca_segundos': ctx.get(f'diferenca_{rotulo}_segundos')}


def _ajuste_solides(a):
    return {'id': a.pk, 'tipo': a.tipo, 'tipo_nome': a.get_tipo_display(), 'dia': a.dia.isoformat(),
            'resumo': a.resumo, 'motivo': a.motivo, 'status': a.status, 'status_nome': a.get_status_display(),
            'resposta': a.resposta or None, 'criado_em': dados.iso(a.criado_em),
            'pode_cancelar': a.status == a.PENDENTE and a.solicitante_id == a.usuario_id}


def painel_do_ponto(user, config):
    """O mesmo que ``tangerino.views.meu_ponto`` monta (tangerino/views.py:173-231), em JSON.

    Reaproveita as funções da própria view e o cache que ela usa (marcações com ttl=120).
    """
    from tangerino import ajustes as ajustes_svc
    from tangerino import ponto as ponto_svc
    from tangerino.client import TangerinoError, integracao_ativa, listar_marcacoes
    from tangerino.models import nao_bate_ponto
    from tangerino.views import _dias_com_marcacoes, _jornada_do_usuario, _previsto_x_feito, _resumo_da_jornada

    hoje = timezone.localdate()
    inicio = hoje - timedelta(days=hoje.weekday())
    eid = user.tangerino_employee_id
    saida = {
        'hoje': hoje.isoformat(),
        'inicio_semana': inicio.isoformat(),
        'integracao_ativa': integracao_ativa(),
        'vinculado': bool(eid),
        'isento': nao_bate_ponto(user),
        'erro': None,
        'status': None,
        'pendencias': [],
        'dias': [],
        'semana': None,
        'periodo': None,
        'jornada': None,
        'tem_jornada': False,
        'ajustes_solides': {'ligado': False, 'pedidos': []},
    }
    if saida['vinculado'] and saida['integracao_ativa']:
        try:
            pares = listar_marcacoes(inicio - timedelta(days=28), hoje, employee_id=eid, ttl=120)
            status = ponto_svc.status_do_dia(eid, dia=hoje, pares=pares)
            saida['status'] = {
                'situacao': status['situacao'],
                'rotulo': status['rotulo'],
                'eventos': [_evento(e) for e in status['eventos']],
                'trabalhado': status['trabalhado_hhmm'],
                'trabalhado_segundos': status['trabalhado_segundos'],
                'dentro': status['dentro'],
                'desde': dados.iso(status['desde']),
                'marcos': [{'rotulo': 'Entrada', 'feito': status['bateu_entrada']},
                           {'rotulo': 'Saída para intervalo', 'feito': status['saiu_almoco']},
                           {'rotulo': 'Volta do intervalo', 'feito': status['voltou_almoco']}],
            }
            saida['pendencias'] = [{'dia': p['dia'].isoformat(), 'entrada': dados.iso(p['entrada']),
                                    'hora': _hhmm(p['entrada'])}
                                   for p in ponto_svc.pendencias(eid, pares=pares)]
            grade = _jornada_do_usuario(user)
            saida['tem_jornada'] = bool(grade)
            dias = _dias_com_marcacoes(pares, inicio - timedelta(days=28), hoje, grade=grade, employee_id=eid)
            semana = [d for d in dias if d['dia'] >= inicio]
            ctx = {}
            ctx.update(_previsto_x_feito(semana, hoje, rotulo='semana'))
            ctx.update(_previsto_x_feito(dias, hoje, rotulo='periodo'))
            saida['dias'] = [_dia(d) for d in dias]
            saida['semana'] = _totais(ctx, 'semana')
            saida['periodo'] = _totais(ctx, 'periodo')
            jornada = _resumo_da_jornada(user)
            if jornada:
                saida['jornada'] = {'nome': jornada.nome, 'horas_semana': jornada.horas_semana,
                                    'resumo': getattr(jornada, 'resumo_semana', '') or ''}
        except TangerinoError as exc:
            saida['erro'] = str(exc)

    if ajustes_svc.ligado(config) and saida['vinculado']:
        saida['ajustes_solides'] = {
            'ligado': True,
            'pedidos': [_ajuste_solides(a) for a in user.ajustes_de_ponto.select_related('decidido_por')[:10]],
        }
    return saida


# ── Endpoints ────────────────────────────────────────────────────────────────

def _exigir_ponto(user):
    config = _config()
    if not ponto_liberado(user, config):
        raise PermissionDenied('O Ponto não está liberado para você.')
    return config


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
def marcacoes(request):
    """Meu ponto: hoje, pendências (dia anterior com entrada sem saída) e os últimos 28 dias."""
    config = _exigir_ponto(request.user)
    return Response(painel_do_ponto(request.user, config))


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
def ajuste_solides_cancelar(request, pk):
    """Cancelar o PRÓPRIO pedido de ajuste que ainda aguarda (tangerino/views.py:1616 → ajustes.cancelar).

    Só muda o status no portal: não fala com a Sólides.
    """
    from tangerino import ajustes as ajustes_svc
    from tangerino.models import SolicitacaoAjustePonto as Sol
    _exigir_ponto(request.user)
    sol = get_object_or_404(Sol, pk=pk, solicitante=request.user)
    try:
        sol = ajustes_svc.cancelar(sol, request.user)
    except ajustes_svc.RegraAjuste as exc:
        return Response({'erro': str(exc)}, status=400)
    return Response(_ajuste_solides(sol))


def _lancamento(l):
    if not l:
        return None
    return {'inicio': l['inicio'].isoformat(), 'fim': l['fim'].isoformat(),
            'dias': (l['fim'] - l['inicio']).days + 1, 'status': l['status']}


def _periodo(p):
    return {'numero': p['numero'], 'inicio': p['inicio'].isoformat(), 'fim': p['fim'].isoformat(),
            'completo': p['completo'], 'concessivo_fim': p['concessivo_fim'].isoformat(),
            'direito': p['direito'], 'gozados': p['gozados'], 'saldo': p['saldo'],
            'vencido': p['vencido'], 'vencendo': p['vencendo'], 'sem_dados': p['sem_dados'],
            'dias_para_vencer': p['dias_para_vencer']}


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
def ferias(request):
    """Minhas férias: o mesmo ``ferias.situacao_do_usuario`` da tela /ferias/ (tangerino/views.py:888)."""
    from tangerino import ferias as ferias_svc
    from tangerino.client import integracao_ativa
    if not ferias_liberadas(request.user):
        raise PermissionDenied('As Férias não estão liberadas para você.')
    s = ferias_svc.situacao_do_usuario(request.user)
    saida = {'integracao_ativa': integracao_ativa(), 'vinculado': bool(request.user.tangerino_employee_id),
             'disponivel': bool(s.get('disponivel')), 'motivo': s.get('motivo')}
    if s.get('disponivel'):
        saida.update({
            'admissao': dados.iso(s['admissao']),
            'saldo_total': s['saldo_total'],
            'dias_vencidos': s['dias_vencidos'],
            'em_gozo': _lancamento(s['em_gozo']),
            'volta_em': dados.iso(s['volta_em']),
            'dias_restantes_gozo': s['dias_restantes_gozo'],
            'proxima': _lancamento(s['proxima']),
            'programadas': [_lancamento(l) for l in s['programadas']],
            'passadas': [_lancamento(l) for l in reversed(s['passadas'])][:10],
            'periodos': [_periodo(p) for p in reversed(s['periodos'])],
            'vencendo': len(s['vencendo']),
        })
    return Response(saida)


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('ponto')])
def escala(request):
    """A escala da semana da própria pessoa (Escala/EscalaDia do portal; nada vai ao Tangerino)."""
    from tangerino import escala as escala_svc
    from tangerino.models import HORAS_SEMANAIS, Escala
    hoje = timezone.localdate()
    try:
        base = date.fromisoformat(request.query_params.get('inicio') or '') if request.query_params.get('inicio') else hoje
    except ValueError:
        base = hoje
    semana_inicio = escala_svc.monday_of(base)
    dias = [semana_inicio + timedelta(days=i) for i in range(7)]
    obj = (Escala.objects.filter(colaborador=request.user, semana_inicio=semana_inicio)
           .prefetch_related('dias').first())
    por_data = {d.data: d for d in obj.dias.all()} if obj else {}
    lista = []
    for i, d in enumerate(dias):
        ed = por_data.get(d)
        lista.append({
            'data': d.isoformat(), 'nome': DIAS_PT[i], 'hoje': d == hoje,
            'tipo': ed.tipo if ed and ed.preenchido else None,
            'entrada': _hhmm_t(ed.entrada) if ed else None,
            'saida_almoco': _hhmm_t(ed.saida_almoco) if ed else None,
            'volta_almoco': _hhmm_t(ed.volta_almoco) if ed else None,
            'saida': _hhmm_t(ed.saida) if ed else None,
            'observacao': (ed.observacao or None) if ed else None,
            'horas': round(ed.minutos / 60, 2) if ed else 0,
        })
    minutos = sum(por_data[d].minutos for d in dias if por_data.get(d))
    return Response({
        'semana_inicio': semana_inicio.isoformat(),
        'semana_fim': (semana_inicio + timedelta(days=6)).isoformat(),
        'anterior': (semana_inicio - timedelta(days=7)).isoformat(),
        'proxima': (semana_inicio + timedelta(days=7)).isoformat(),
        'dias': lista,
        'tem_escala': any(por_data.get(d) and por_data[d].preenchido for d in dias),
        'horas': round(minutos / 60, 2),
        'horas_meta': HORAS_SEMANAIS,
        'abaixo_da_meta': minutos < HORAS_SEMANAIS * 60,
        # Quem monta escala (gerente/gestor) vê a grade da equipe no portal; aqui é só a própria.
        'monta_escala': escala_svc.pode_gerenciar(request.user),
    })


def _hhmm_t(t):
    return t.strftime('%H:%M') if t else None
