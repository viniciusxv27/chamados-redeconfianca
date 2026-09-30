"""Cartão "quiz aguardando você" na home do portal.

A home (`communications.views.home_feed`) não sabe nada do quiz: o template
inclui `quiz/_home.html` só quando o context processor já disse que a pessoa
tem sala aberta (`quiz_salas_abertas`), e a tag `cartao_quiz_home` renderiza.

Regras, iguais às do cartão da rotina:
- uma consulta por renderização, e só para quem tem sala aberta;
- qualquer erro vira cartão ausente, com o traceback no log — a home nunca cai
  por causa do quiz.
"""
import logging

from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)

TEMPLATE_CARTAO = 'quiz/_cartao_home.html'
LIMITE_DE_SALAS = 3           # o cartão mostra as próximas; o resto fica em /quiz/


def salas_aguardando(user):
    """As salas abertas em que a pessoa é participante, da mais próxima em diante."""
    from .models import Participante, Sala

    participacoes = (Participante.objects
                     .filter(user=user, sala__fase__in=Sala.ABERTAS)
                     .select_related('sala', 'sala__quiz')
                     .order_by('sala__agendada_para'))
    agora = timezone.localtime()
    salas = []
    for p in participacoes[:LIMITE_DE_SALAS]:
        sala = p.sala
        salas.append({
            'sala': sala,
            'ao_vivo': sala.ao_vivo,
            'ja_entrou': bool(p.entrou_em),
            'quando': timezone.localtime(sala.agendada_para),
            'hoje': timezone.localtime(sala.agendada_para).date() == agora.date(),
            'atrasada': sala.fase == sala.Fase.AGENDADA and sala.agendada_para < timezone.now(),
        })
    return salas


def html_do_cartao(user):
    """O cartão já renderizado. Sem `request` de propósito: renderizar com ele
    rodaria de novo todos os context processors (e as consultas deles)."""
    salas = salas_aguardando(user)
    if not salas:
        return ''
    return render_to_string(TEMPLATE_CARTAO, {'qz_salas': salas})


def cartao_seguro(user):
    """Como `html_do_cartao`, mas nunca levanta: na falha devolve '' e registra no log."""
    try:
        return html_do_cartao(user)
    except Exception:                                           # noqa: BLE001
        logger.exception('Cartão do quiz na home não carregou (usuário %s)',
                         getattr(user, 'pk', None))
        return ''
