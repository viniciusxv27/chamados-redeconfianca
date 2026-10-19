"""A projeção de cada pessoa no simulador, gravada no banco todo dia às 08:00.

Quem dispara é o agendador diário (core/agendador_diario.py). A conta é a da
tabela da rede (simulator/tabela.py ``construir_linhas``); aqui ela só ganha
um lugar fixo no banco (``ProjecaoSimulador``), um retrato por dia, e de
quebra deixa a tabela da tela já montada no cache.
"""
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import ProjecaoSimulador
from .tabela import DATASET_KEY, DATASET_TTL, ERROR_KEY, construir_linhas


def _dinheiro(valor):
    return Decimal(str(round(float(valor or 0), 2)))


def gravar(linhas, referencia=None):
    """Troca o retrato do dia pelas linhas calculadas."""
    referencia = referencia or timezone.localdate()
    novas = []
    vistos = set()
    for l in linhas:
        if l.get('id') in vistos:
            continue
        vistos.add(l.get('id'))
        novas.append(ProjecaoSimulador(
            referencia=referencia, user_id=l.get('id'), nome=(l.get('name') or '')[:200],
            papel=(l.get('role_key') or '')[:30], papel_rotulo=(l.get('role_label') or '')[:60],
            loja=(l.get('sector') or '')[:120], setor_id=l.get('sector_id'),
            coordenador=(l.get('coordinator') or '')[:200],
            realizado=_dinheiro(l.get('realizado')), projecao=_dinheiro(l.get('projecao')),
            diferenca=_dinheiro(l.get('diferenca')), motivo=(l.get('motivo') or '')[:500],
            fatores=l.get('fatores') or [], tem_dados=bool(l.get('has_data')), erro=(l.get('erro') or '')[:200]))
    with transaction.atomic():
        ProjecaoSimulador.objects.filter(referencia=referencia).delete()
        ProjecaoSimulador.objects.bulk_create(novas, batch_size=500)
    return len(novas)


def gerar_projecao_diaria():
    """Calcula todo mundo, grava no banco e aquece a tabela da tela."""
    linhas = construir_linhas(force_refresh=True)
    total = gravar(linhas)
    cache.set(DATASET_KEY, {'rows': linhas, 'generated_at': timezone.now(), 'built_on': timezone.localdate()},
              DATASET_TTL)
    cache.delete(ERROR_KEY)
    com_dados = sum(1 for l in linhas if l.get('has_data'))
    return f'{total} pessoas gravadas ({com_dados} com dados) em {timezone.localdate():%d/%m/%Y}.'
