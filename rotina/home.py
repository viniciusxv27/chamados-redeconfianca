"""Cartão "Rotina gerencial" na home do portal.

A home (communications.views.home_feed) não sabe nada da rotina: o template
inclui `rotina/_home.html` só quando o context processor já disse que a
pessoa tem rotina liberada, e a tag `cartao_rotina_home` renderiza o cartão.

Regras do cartão:
- no máximo uma consulta por renderização (`servicos.cartao_da_home`);
- qualquer erro vira cartão ausente, com o traceback no log — a home nunca cai
  por causa da rotina;
- o mesmo HTML serve à home e ao `api/hoje/cartao/`, que o cartão chama para
  se redesenhar quando o dia muda de atividade.
"""
import logging

from django.template.loader import render_to_string

from . import servicos

logger = logging.getLogger(__name__)

TEMPLATE_CARTAO = 'rotina/_cartao_home.html'


def html_do_cartao(user, momento=None):
    """O cartão já renderizado. Sem `request` de propósito: renderizar com ele
    rodaria de novo todos os context processors (e as consultas deles)."""
    return render_to_string(TEMPLATE_CARTAO, {'rh': servicos.cartao_da_home(user, momento)})


def cartao_seguro(user, momento=None):
    """Como `html_do_cartao`, mas nunca levanta: na falha devolve '' e registra no log."""
    try:
        return html_do_cartao(user, momento)
    except Exception:                                           # noqa: BLE001
        logger.exception('Cartão da rotina gerencial na home não carregou (usuário %s)',
                         getattr(user, 'pk', None))
        return ''
