"""Tabela da rede: realizado, projeção, diferença e o motivo, por colaborador.

Pedido: "preciso de uma visão de tabela, de todos os colaboradores (resultado
realizado e projeção, e a diferença e o motivo)".

É o mesmo motor de ``/simulator/`` rodado duas vezes para cada pessoa — uma no
modo Realizado e outra no modo Projeção — e a comparação das duas explica a
diferença: quais pilares crescem até o fim do mês e quais aceleradores só
entram na projeção.

Custo: 167 pessoas × 2 cálculos, cada um com planilha, MySQL e metas — 86 s
medidos na rede inteira em 28/09/2026. Nunca roda dentro da requisição de quem
abriu a tela: vale a mesma receita das médias (``simulator/averages.py``) — um
``realized_prefetch`` para o lote todo, cache do dia e recálculo em segundo
plano servindo o número de ontem enquanto isso.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from django.core.cache import cache
from django.utils import timezone

from users.models import User

from .averages import (
    ROLE_LABELS,
    _factor_data_for,
    _fill_missing_coordinators,
    build_roster,
)
from .services import (
    ROLE_APART,
    ROLE_CONSULTOR,
    ROLE_COORDENADOR,
    ROLE_GERENTE,
    VIEW_PROJECAO,
    VIEW_REALIZADO,
    compute_aparte_simulation,
    compute_consultor_simulation,
    compute_coordenador_simulation,
    compute_gerente_simulation,
    get_store_name_from_user,
)
from .sql_realizado import realized_prefetch

logger = logging.getLogger(__name__)

DATASET_KEY = 'simulator_tabela_dataset'
ERROR_KEY = 'simulator_tabela_last_error'
DATASET_TTL = 7 * 86400
ERROR_BACKOFF = 300

# Diferença de menos de um real não é motivo de nada — é arredondamento.
MINIMO_RELEVANTE = 1.0
# Quantos fatores cabem no resumo da coluna "Motivo" (o resto abre no detalhe).
FATORES_NO_RESUMO = 3

# Os ganhos que não vêm de um pilar: entram (ou não) conforme o atingimento.
EXTRAS = (
    ('hunter2', 'Hunter II'),
    ('hunter3', 'Hunter III'),
    ('bonus_6_7', 'Bônus 6/7'),
    ('acelerador_microindicadores', 'Acelerador B2B'),
)


def _simular(user: User, role: str, factors: Dict[str, Any], view_mode: str,
             config_aparte: Any = None) -> Optional[Dict[str, Any]]:
    """Roda o simulador de um jeito só, mudando a visão.

    ``config_aparte`` chega resolvido de fora porque a mesma pessoa é calculada
    duas vezes (realizado e projeção) e a configuração não muda entre as duas.
    """
    if role == ROLE_APART:
        return compute_aparte_simulation(user, config_aparte, {}, view_mode=view_mode)

    dados = _factor_data_for(role, factors)
    if role == ROLE_CONSULTOR:
        return compute_consultor_simulation(user, dados, {}, view_mode=view_mode)
    if role == ROLE_GERENTE:
        return compute_gerente_simulation(user, dados, {}, view_mode=view_mode)
    if role == ROLE_COORDENADOR:
        return compute_coordenador_simulation(user, dados, {}, view_mode=view_mode)
    return None


def _ganho(simulacao: Optional[Dict[str, Any]]) -> float:
    if not simulacao or simulacao.get('error'):
        return 0.0
    return float((simulacao.get('totals') or {}).get('ganho_total') or 0.0)


def _pilares(simulacao: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{chave do pilar: como ele está} — o que dá para comparar entre as visões."""
    saida: Dict[str, Dict[str, Any]] = {}
    if not simulacao or simulacao.get('error'):
        return saida
    for linha in simulacao.get('rows') or []:
        chave = linha.get('key')
        if not chave:
            continue
        # Quanto o pilar põe no bolso. Cada papel expõe isso num campo
        # diferente: o consultor em `total_with_pdv` (comissão + prêmio
        # individual + prêmio de PDV), o coordenador e o "A parte" só em
        # `commission_value`. E a linha que junta eletrônicos/essenciais A+B
        # (`_merge_grouped_rows`) chega com `total_with_pdv` em 0 e a comissão
        # cheia em `commission_value`. Como os três campos são uma soma do
        # anterior e nenhum é negativo, o maior deles é o valor completo.
        saida[chave] = {
            'label': linha.get('label') or chave,
            'ganho': max(float(linha.get('total_with_pdv') or 0.0),
                         float(linha.get('total_individual') or 0.0),
                         float(linha.get('commission_value') or 0.0)),
            'meta': float(linha.get('meta') or 0.0),
            'proj': float(linha.get('proj') or linha.get('quantity') or 0.0),
            # Fração, como no resto do simulador: 0,88 é 88% da meta.
            'attainment': float(linha.get('attainment') or 0.0),
        }
    return saida


def _extras(simulacao: Optional[Dict[str, Any]]) -> Dict[str, float]:
    totais = (simulacao or {}).get('totals') or {}
    return {chave: float(totais.get(chave) or 0.0) for chave, _rotulo in EXTRAS}


def comparar(realizado: Optional[Dict[str, Any]],
             projecao: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """O que explica a diferença entre o realizado e a projeção, do maior para o menor."""
    pilares_real, pilares_proj = _pilares(realizado), _pilares(projecao)
    extras_real, extras_proj = _extras(realizado), _extras(projecao)

    fatores: List[Dict[str, Any]] = []
    for chave in dict.fromkeys(list(pilares_proj) + list(pilares_real)):
        proj = pilares_proj.get(chave) or {}
        real = pilares_real.get(chave) or {}
        delta = (proj.get('ganho') or 0.0) - (real.get('ganho') or 0.0)
        if abs(delta) < MINIMO_RELEVANTE:
            continue
        fatores.append({
            'tipo': 'pilar',
            'label': proj.get('label') or real.get('label') or chave,
            'delta': delta,
            'realizado': real.get('ganho') or 0.0,
            'projecao': proj.get('ganho') or 0.0,
            'atingimento': proj.get('attainment') or 0.0,
        })

    for chave, rotulo in EXTRAS:
        delta = extras_proj.get(chave, 0.0) - extras_real.get(chave, 0.0)
        if abs(delta) < MINIMO_RELEVANTE:
            continue
        fatores.append({
            'tipo': 'extra',
            'label': rotulo,
            'delta': delta,
            'realizado': extras_real.get(chave, 0.0),
            'projecao': extras_proj.get(chave, 0.0),
            'atingimento': 0.0,
        })

    fatores.sort(key=lambda f: abs(f['delta']), reverse=True)
    return fatores


def resumir_motivo(fatores: List[Dict[str, Any]], tem_dados: bool, diferenca: float,
                   zerado: bool = False) -> str:
    """A frase da coluna "Motivo": o que mais pesa na diferença."""
    if not tem_dados:
        return 'Sem dados no mês'
    if zerado:
        return 'Sem venda no mês'
    if abs(diferenca) < MINIMO_RELEVANTE:
        return 'Projeção igual ao realizado'
    if not fatores:
        return 'Diferença de arredondamento'
    partes = []
    for fator in fatores[:FATORES_NO_RESUMO]:
        sinal = '+' if fator['delta'] >= 0 else '−'
        partes.append(f"{fator['label']} {sinal}R$ {abs(fator['delta']):,.0f}"
                      .replace(',', '.'))
    if len(fatores) > FATORES_NO_RESUMO:
        partes.append(f'e mais {len(fatores) - FATORES_NO_RESUMO}')
    return ' · '.join(partes)


def _linha(entrada: Dict[str, Any], factors: Dict[str, Any]) -> Dict[str, Any]:
    user: User = entrada['user']
    papel: str = entrada['role']
    nome = user.get_full_name() or user.email

    linha = {
        'id': user.id,
        'name': nome,
        'initials': (f"{(user.first_name or '')[:1]}{(user.last_name or '')[:1]}".strip().upper()
                     or (user.email or '?')[:1].upper()),
        'role_key': papel,
        'role_label': ROLE_LABELS.get(papel, papel),
        'sector': get_store_name_from_user(user),
        'sector_id': user.sector_id,
        'coordinator': '',
        'realizado': 0.0,
        'projecao': 0.0,
        'diferenca': 0.0,
        'fatores': [],
        'motivo': 'Sem dados no mês',
        'has_data': False,
        'erro': '',
    }

    config_aparte = None
    if papel == ROLE_APART:
        from users.models import AParteCommissionConfig
        config_aparte = AParteCommissionConfig.objects.filter(user=user).first()

    try:
        realizado = _simular(user, papel, factors, VIEW_REALIZADO, config_aparte)
        projecao = _simular(user, papel, factors, VIEW_PROJECAO, config_aparte)
    except Exception:
        logger.exception('Falha ao montar a linha de %s (%s)', nome, papel)
        linha['erro'] = 'Não deu para calcular'
        return linha

    erro = (realizado or {}).get('error') or (projecao or {}).get('error')
    if erro:
        linha['erro'] = str(erro)[:200]

    linha['realizado'] = _ganho(realizado)
    linha['projecao'] = _ganho(projecao)
    linha['diferenca'] = linha['projecao'] - linha['realizado']
    linha['has_data'] = bool((realizado and not realizado.get('error'))
                             or (projecao and not projecao.get('error')))
    linha['fatores'] = comparar(realizado, projecao)
    linha['motivo'] = resumir_motivo(
        linha['fatores'], linha['has_data'], linha['diferenca'],
        zerado=not linha['realizado'] and not linha['projecao'])

    fonte = projecao if (projecao and not projecao.get('error')) else realizado
    if fonte:
        linha['coordinator'] = (fonte.get('coordinator') or '').strip().upper()
        linha['sector'] = linha['sector'] or (fonte.get('pdv') or '').strip().upper()
    return linha


def construir_linhas(roster: Optional[List[Dict[str, Any]]] = None,
                     force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Uma linha por colaborador, da maior projeção para a menor."""
    roster = roster if roster is not None else build_roster()
    agora = timezone.now()
    factors: Dict[str, Any] = {}
    linhas: List[Dict[str, Any]] = []

    with realized_prefetch(agora.year, agora.month, force_refresh=force_refresh):
        for entrada in roster:
            linhas.append(_linha(entrada, factors))

    _fill_missing_coordinators(linhas)
    linhas.sort(key=lambda l: l['projecao'], reverse=True)
    return linhas


# ---------------------------------------------------------------------------
# Cache do dia, com recálculo em segundo plano (igual ao das médias)
# ---------------------------------------------------------------------------
_TRAVA = threading.Lock()
_montando = False


def _montar_dataset(force_refresh: bool = False) -> Dict[str, Any]:
    linhas = construir_linhas(force_refresh=force_refresh)
    dataset = {'rows': linhas, 'generated_at': timezone.now(), 'built_on': timezone.localdate()}
    cache.set(DATASET_KEY, dataset, DATASET_TTL)
    cache.delete(ERROR_KEY)
    return dataset


def _recalcular_em_segundo_plano(force_refresh: bool = False) -> bool:
    global _montando
    with _TRAVA:
        if _montando:
            return False
        _montando = True

    def rodar() -> None:
        global _montando
        try:
            _montar_dataset(force_refresh=force_refresh)
        except Exception:
            logger.exception('Falha ao montar a tabela do simulador')
            cache.set(ERROR_KEY, True, ERROR_BACKOFF)
        finally:
            from django.db import connections
            connections.close_all()
            with _TRAVA:
                _montando = False

    threading.Thread(target=rodar, daemon=True, name='simulator-tabela').start()
    return True


def get_tabela_dataset(force_refresh: bool = False) -> Dict[str, Any]:
    """A tabela da rede: do cache, recalculando em segundo plano quando envelhece."""
    dataset = cache.get(DATASET_KEY)
    vencido = dataset is not None and dataset.get('built_on') != timezone.localdate()
    esperando_erro = cache.get(ERROR_KEY) is not None

    if force_refresh or ((dataset is None or vencido) and not esperando_erro):
        _recalcular_em_segundo_plano(force_refresh=force_refresh)

    if dataset is None:
        return {'rows': [], 'generated_at': None, 'built_on': None,
                'status': 'building', 'is_stale': True}
    return {
        'rows': dataset['rows'],
        'generated_at': dataset['generated_at'],
        'built_on': dataset.get('built_on'),
        'status': 'ready',
        'is_stale': vencido or force_refresh,
    }


def resumir(linhas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Os totais do rodapé da tabela."""
    realizado = sum(l['realizado'] for l in linhas)
    projecao = sum(l['projecao'] for l in linhas)
    com_dados = [l for l in linhas if l['has_data']]
    return {
        'pessoas': len(linhas),
        'com_dados': len(com_dados),
        'realizado': realizado,
        'projecao': projecao,
        'diferenca': projecao - realizado,
        'media_projecao': (projecao / len(com_dados)) if com_dados else 0.0,
    }
