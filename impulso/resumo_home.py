"""Cartão do Impulso na home (25/11/2026): onde a pessoa está no mês e o que fazer em seguida.

Pedido: "para as pessoas que têm, deve aparecer um card do Impulso com um resuminho para ajudar a
localizar a pessoa, com pontuação e dicas da próxima coisa que deve fazer".

- Quem vê: ``is_impulso_member`` (o mesmo do menu).
- Pontuação: a do topo de /impulso/acompanhamento/ (``calcular_pontuacao`` no mês em conferência).
- Posição: a do ranking do acompanhamento, lida do cache que o app já usa
  (``api_mobile.modulos.impulso._ranking_do_periodo``). Calcular o ranking inteiro leva ~20 s, então
  a home nunca espera por ele: se não está pronto, manda calcular numa thread e mostra na próxima visita.
- Dicas: primeiro o que é urgente (meta atrasada, vence hoje, convite, entrega para avaliar), depois os
  itens da pontuação com mais pontos em aberto, cada um com o link de onde se resolve.

A home pede o cartão depois de carregar (``impulso:resumo_home``) e ele fica guardado 2 min por pessoa.
"""
import logging
import threading
from decimal import Decimal

from django.core.cache import cache
from django.db import close_old_connections
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

GUARDA_SEGUNDOS = 120
_calculando_ranking = threading.Lock()

# Item da pontuação (impulso.scoring.linhas_detalhadas) → dica e onde resolver.
DICAS_DOS_ITENS = {
    'Metas — conclusão': ('fa-bullseye', 'Conclua as metas do mês', 'impulso:metas_kanban'),
    'Metas — qualidade das entregas': ('fa-star', 'Capriche na entrega das metas: qualidade e prazo contam', 'impulso:metas_kanban'),
    'Feedback do gestor': ('fa-comments', 'Combine um feedback com o seu gestor', 'impulso:feedback_list'),
    'Curso do mês': ('fa-graduation-cap', 'Faça o curso do mês no Conectar', 'impulso:conectar_list'),
    'Vídeos e POPs': ('fa-play-circle', 'Assista aos vídeos e POPs do Conectar', 'impulso:conectar_list'),
    'Projeto foco': ('fa-diagram-project', 'Avance no projeto foco', 'impulso:conectar_list'),
    'Projeto aprovado': ('fa-rocket', 'Transforme uma ideia em projeto aprovado no Inovar', 'impulso:inovar_list'),
}
PROXIMA_FAIXA = (('BRONZE', 'Prata', Decimal('70.01')), ('PRATA', 'Ouro', Decimal('90')), ('OURO', 'Impulso', Decimal('100')))


def _url(nome):
    try:
        return reverse(nome)
    except Exception:  # noqa: BLE001 — rota que não existe nesta instalação: a dica vai sem link
        return ''


def _posicao(user, inicio, fim):
    """(posição, total) do ranking já calculado, ou None (e manda calcular em segundo plano)."""
    chave = f'api_mobile:impulso:ranking:{inicio:%Y%m%d}:{fim:%Y%m%d}'
    pronto = cache.get(chave)
    if pronto is None:
        _calcular_ranking_em_segundo_plano(inicio, fim)
        return None
    for n, linha in enumerate(pronto['linhas'], start=1):
        if linha['id'] == user.pk:
            return n, len(pronto['linhas'])
    return None


def _calcular_ranking_em_segundo_plano(inicio, fim):
    if not _calculando_ranking.acquire(blocking=False):
        return

    def _rodar():
        try:
            close_old_connections()
            from api_mobile.modulos.impulso import _ranking_do_periodo
            _ranking_do_periodo(inicio, fim)
        except Exception:  # noqa: BLE001
            logger.warning('Impulso: ranking da home não calculado', exc_info=True)
        finally:
            close_old_connections()
            _calculando_ranking.release()

    threading.Thread(target=_rodar, daemon=True, name='impulso-ranking-home').start()


def _dicas(user, dados, contagem, para_avaliar, convites):
    from impulso.scoring import linhas_detalhadas
    kanban = _url('impulso:metas_kanban')
    dicas = []
    if contagem['atrasadas']:
        n = contagem['atrasadas']
        dicas.append({'icone': 'fa-triangle-exclamation', 'tom': 'vermelho', 'url': kanban,
                      'texto': f'{n} meta{"s" if n > 1 else ""} atrasada{"s" if n > 1 else ""}: atualize ou entregue'})
    if contagem['vencem_hoje']:
        n = contagem['vencem_hoje']
        dicas.append({'icone': 'fa-clock', 'tom': 'ambar', 'url': kanban,
                      'texto': f'{n} meta{"s" if n > 1 else ""} vence{"m" if n > 1 else ""} hoje'})
    if para_avaliar:
        dicas.append({'icone': 'fa-clipboard-check', 'tom': 'azul', 'url': kanban,
                      'texto': f'{para_avaliar} entrega{"s" if para_avaliar > 1 else ""} da equipe esperando sua avaliação'})
    if convites:
        dicas.append({'icone': 'fa-handshake', 'tom': 'azul', 'url': kanban,
                      'texto': f'Responda {convites} convite{"s" if convites > 1 else ""} de parceria'})
    # Os itens da pontuação com mais pontos em aberto (o que ainda dá para ganhar no mês).
    abertos = []
    for linha in linhas_detalhadas(dados):
        falta = Decimal(str(linha['max'] or 0)) - Decimal(str(linha['pontos'] or 0))
        info = str(linha.get('info') or '')
        if falta <= 0 or info.startswith('Nenhum') or info.startswith('Sem '):
            continue                       # nada a fazer, ou o item não existiu no mês (não é culpa de ninguém)
        if linha['item'].startswith('Propor'):
            modelo = ('fa-lightbulb', 'Mande suas ideias no Inovar', 'impulso:inovar_list')
        elif linha['item'].startswith('Assiduidade'):
            modelo = ('fa-user-clock', 'Bata o ponto certinho: ajuste de ponto tira pontos', 'impulso:assiduidade')
        else:
            modelo = DICAS_DOS_ITENS.get(linha['item'])
        if not modelo:
            continue
        icone, texto, rota = modelo
        abertos.append((falta, {'icone': icone, 'tom': 'laranja', 'url': _url(rota),
                                'texto': texto, 'pontos': f'{falta:.0f}'.replace('.', ',')}))
    abertos.sort(key=lambda par: -par[0])
    vistos = {d['texto'] for d in dicas}
    for _falta, dica in abertos:
        if dica['texto'] not in vistos:
            dicas.append(dica)
            vistos.add(dica['texto'])
    return dicas[:3]


def resumo(user):
    """Tudo o que o cartão mostra (guardado 2 min por pessoa)."""
    chave = f'impulso:resumo_home:v1:{user.pk}'
    guardado = cache.get(chave)
    if guardado is not None:
        return guardado

    from impulso.models import Meta, ParceriaMeta
    from impulso.scoring import blocos_resumo, calcular_pontuacao
    from impulso.utils import faixa_info, is_impulso_manager
    from impulso.views import _periodo_do_acompanhamento

    inicio, fim, mes_ciclo = _periodo_do_acompanhamento()
    dados = calcular_pontuacao(user, inicio=inicio, fim=fim)
    hoje = timezone.localdate()
    minhas = (Meta.objects.filter(Q(colaborador=user) | Q(participantes=user), aprovacao=Meta.Aprovacao.APROVADA)
              .exclude(status=Meta.Status.CONCLUIDA).distinct())
    contagem = {
        'abertas': minhas.count(),
        'atrasadas': minhas.filter(prazo__lt=hoje).count(),
        'vencem_hoje': minhas.filter(prazo=hoje).count(),
    }
    gestor = is_impulso_manager(user)
    para_avaliar = Meta.objects.filter(gestor=user, status=Meta.Status.ENTREGUE).count() if gestor else 0
    convites = (ParceriaMeta.objects.filter(convidado=user, status=ParceriaMeta.Status.PENDENTE)
                .exclude(meta__aprovacao=Meta.Aprovacao.RECUSADA).count())

    percentual = Decimal(str(dados['percentual']))
    faixa = faixa_info(dados['faixa'])
    proxima = next(((nome, alvo) for atual, nome, alvo in PROXIMA_FAIXA if atual == dados['faixa']), None)
    meses = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
             'outubro', 'novembro', 'dezembro']
    pronto = {
        'mes': f'{meses[inicio.month - 1]}',
        'em_conferencia': bool(mes_ciclo and fim < hoje),
        'percentual': float(percentual),
        'total': float(dados['total']), 'maximo': float(dados['aplicavel']),
        'faixa': {'nome': faixa['label'], 'cor': faixa['cor'], 'icone': faixa['icon'], 'grad': faixa['grad']},
        'proxima': ({'nome': proxima[0], 'falta': float(max(Decimal('0'), proxima[1] - percentual))}
                    if proxima else None),
        'blocos': [{'nome': b['nome'].title(), 'cor': b['cor'], 'icone': b['icone'], 'pct': b['pct'],
                    'valor': float(b['valor']), 'max': float(b['max'])} for b in blocos_resumo(dados)],
        'metas': contagem,
        'dicas': _dicas(user, dados, contagem, para_avaliar, convites),
    }
    cache.set(chave, pronto, GUARDA_SEGUNDOS)
    return pronto


def contexto_do_cartao(user):
    """O resumo com a posição do momento (a posição não entra no que fica guardado)."""
    from impulso.views import _periodo_do_acompanhamento
    dados = resumo(user)
    inicio, fim, _mes = _periodo_do_acompanhamento()
    posicao = _posicao(user, inicio, fim)
    return {**dados, 'posicao': posicao[0] if posicao else None, 'participantes': posicao[1] if posicao else None}
