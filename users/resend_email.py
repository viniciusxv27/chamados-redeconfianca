"""Envio de e-mail pela Resend.

Usa a API HTTP direto, sem pacote novo: é uma chamada só, e `requests` já está
no projeto. A chave vem do .env — nunca do código.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

URL = 'https://api.resend.com/emails'
TIMEOUT = 15


def configurada():
    """Dá para enviar? Sem chave, quem chama decide o que dizer ao usuário."""
    return bool((getattr(settings, 'RESEND_API_KEY', '') or '').strip())


def enviar(para, assunto, html, texto=''):
    """Envia um e-mail. Devolve (ok, erro_legivel).

    Nunca levanta exceção: quem chama está no meio de um fluxo de usuário e
    precisa decidir a mensagem da tela, não receber um stack trace.
    """
    if not configurada():
        return False, 'RESEND_API_KEY não configurada no .env.'

    corpo = {
        'from': getattr(settings, 'RESEND_FROM', '') or 'onboarding@resend.dev',
        'to': [para] if isinstance(para, str) else list(para),
        'subject': assunto,
        'html': html,
    }
    if texto:
        corpo['text'] = texto

    try:
        resposta = requests.post(
            URL,
            headers={'Authorization': f'Bearer {settings.RESEND_API_KEY}',
                     'Content-Type': 'application/json'},
            json=corpo, timeout=TIMEOUT)
    except requests.RequestException as erro:
        logger.warning('Resend indisponível: %s', erro)
        return False, 'Não foi possível falar com o serviço de e-mail.'

    if resposta.status_code in (200, 201):
        return True, ''

    # O detalhe da recusa vai para o log, não para a tela: a resposta da Resend
    # pode citar domínio e remetente, que não interessam a quem esqueceu a senha.
    try:
        detalhe = resposta.json().get('message') or resposta.text[:200]
    except ValueError:
        detalhe = resposta.text[:200]
    logger.error('Resend recusou o envio (%s): %s', resposta.status_code, detalhe)

    if resposta.status_code == 403 and ('testing emails' in detalhe.lower()
                                        or 'own email address' in detalhe.lower()):
        # Caso clássico de conta nova: sem domínio verificado, a Resend só
        # entrega no e-mail do dono da conta. É config, não erro de quem digitou.
        return False, ('O envio de e-mail ainda está em modo de teste na Resend: '
                       'só chega no endereço do dono da conta. '
                       'Peça ao administrador para verificar o domínio.')
    if resposta.status_code in (401, 403):
        return False, 'A chave da Resend foi recusada. Avise o administrador.'
    if resposta.status_code == 422:
        return False, ('O remetente não está verificado na Resend. '
                       'Avise o administrador.')
    return False, 'O serviço de e-mail recusou o envio. Tente de novo em instantes.'
