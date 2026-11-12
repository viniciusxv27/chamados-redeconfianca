"""O ponto do Tangerino (``tangerino.MarcacaoPonto``) como segunda testemunha da câmera.

A câmera vê "uma pessoa"; o ponto diz quem da equipe estava trabalhando ali. Serve para:
- o alerta de loja fechada: com alguém da equipe de ponto batido no horário, é muito provável que seja ela;
- as entradas pela porta: cada entrada no ponto é uma passagem de funcionário que a câmera também contou;
- a matinal: sem ninguém de ponto batido antes do fim da janela, não havia equipe para fazer a matinal.

A loja da pessoa é o setor principal (``user.sector``). Sem setor ligado ao gravador, ou sem ponto
sincronizado, tudo devolve vazio e a tela segue só com a câmera.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

logger = logging.getLogger(__name__)
FOLGA = timedelta(minutes=20)


def _marcacoes(setor_id, de, ate):
    if not setor_id:
        return []
    try:
        from tangerino.models import MarcacaoPonto
        return list(MarcacaoPonto.objects.filter(usuario__sector_id=setor_id, data__gte=de, data__lte=ate)
                    .only('nome', 'data', 'entrada1', 'saida1', 'entrada2', 'saida2', 'entrada3', 'saida3', 'em_aberto'))
    except Exception:  # noqa: BLE001 — app do ponto fora do ar ou sem a tabela: a câmera segue sozinha
        logger.warning('Ponto indisponível para as câmeras', exc_info=True)
        return []


def ultimo_dia(setor_id):
    """Até que dia o ponto da loja já chegou do Tangerino. Depois dele, "ninguém bateu" é só "ainda não sincronizou"."""
    if not setor_id:
        return None
    try:
        from tangerino.models import MarcacaoPonto
        return MarcacaoPonto.objects.filter(usuario__sector_id=setor_id).aggregate(d=Max('data'))['d']
    except Exception:  # noqa: BLE001
        logger.warning('Ponto indisponível para as câmeras', exc_info=True)
        return None


def _pares(m):
    return [(e, s) for e, s in ((m.entrada1, m.saida1), (m.entrada2, m.saida2), (m.entrada3, m.saida3)) if e]


def equipe_no_momento(gravador, momento):
    """[{'nome', 'horarios', 'como'}] de quem da loja estava de ponto batido (ou chegando/saindo) no momento."""
    local = timezone.localtime(momento)
    saida = []
    for m in _marcacoes(gravador.setor_id, local.date(), local.date()):
        como = ''
        for entrada, fim in _pares(m):
            if entrada - FOLGA <= momento <= (fim or momento) + FOLGA:
                como = 'chegando' if abs((entrada - momento).total_seconds()) <= FOLGA.total_seconds() else (
                    'saindo' if fim and abs((fim - momento).total_seconds()) <= FOLGA.total_seconds() else 'trabalhando')
                break
        if como:
            saida.append({'nome': m.nome, 'horarios': m.horarios, 'como': como})
    return saida


def por_dia(gravador, de, ate):
    """{data: {'pessoas', 'entradas', 'primeira', 'antes_da_matinal'}} — 'entradas' são as batidas de entrada com a loja aberta."""
    dias = defaultdict(lambda: {'pessoas': 0, 'entradas': 0, 'primeira': None, 'antes_da_matinal': 0,
                                'por_hora': defaultdict(int)})
    abre, fecha = gravador.abre_as, gravador.fecha_as
    for m in _marcacoes(gravador.setor_id, de, ate):
        dia = dias[m.data]
        pares = _pares(m)
        if not pares:
            continue
        dia['pessoas'] += 1
        primeira = timezone.localtime(pares[0][0]).time()
        dia['primeira'] = min(dia['primeira'] or primeira, primeira)
        if primeira < gravador.matinal_fim:
            dia['antes_da_matinal'] += 1
        for entrada, _fim in pares:
            hora = timezone.localtime(entrada).time()
            if abre <= hora < fecha:
                dia['entradas'] += 1
                dia['por_hora'][hora.hour] += 1
    return dict(dias)
