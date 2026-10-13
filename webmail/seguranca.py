"""Senha da caixa em repouso e o TLS até o servidor de e-mail.

**Senha.** Para ler a caixa a cada tela o portal precisa da senha de volta, então
ela é cifrada (Fernet), não resumida. A chave vem de ``WEBMAIL_CHAVE`` no .env;
sem ela, é derivada do ``SECRET_KEY`` com um sal próprio — o mesmo esquema da
chave do Assistente (assistente/crypto.py), mas com outra chave, para que vazar
uma não abra a outra. A senha nunca volta para a tela.

**TLS.** O poste.io atende o IMAP/SMTP com o certificado que ele mesmo gera
(autoassinado, "O=Poste.io") enquanto o Let's Encrypt não é aplicado aos
serviços de e-mail. Desligar a verificação mandaria a senha para qualquer um no
meio do caminho; então a conexão tenta primeiro a verificação normal e, só se
ela falhar, aceita **exatamente** o certificado cuja impressão digital SHA-256
está em ``WEBMAIL_TLS_SHA256`` (pinagem). Sem pino configurado, não conecta.
Quando o certificado válido for aplicado no poste.io, a verificação normal passa
e o pino deixa de ser usado sozinho.
"""
import base64
import hashlib
import ssl

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class CertificadoNaoConfere(Exception):
    """O servidor apresentou um certificado que nem é válido nem é o pinado."""


def _fernet():
    chave = getattr(settings, 'WEBMAIL_CHAVE', '') or ''
    if chave:
        return Fernet(chave.encode())
    bruto = hashlib.sha256(b'webmail:' + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(bruto))


def cifrar(senha: str) -> str:
    return _fernet().encrypt((senha or '').encode('utf-8')).decode()


def decifrar(token: str) -> str:
    """Devolve a senha, ou '' se a chave mudou (aí a pessoa entra de novo)."""
    try:
        return _fernet().decrypt((token or '').encode()).decode('utf-8')
    except (InvalidToken, ValueError):
        return ''


def _pino():
    return (getattr(settings, 'WEBMAIL_TLS_SHA256', '') or '').replace(':', '').strip().lower()


def contexto_verificado():
    return ssl.create_default_context()


def contexto_pinado():
    """Contexto que aceita o certificado sem cadeia — a conferência é o pino."""
    if not _pino():
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def conferir_pino(sock_tls):
    """Chamado logo depois do handshake e **antes** de mandar a senha."""
    der = sock_tls.getpeercert(binary_form=True) or b''
    if hashlib.sha256(der).hexdigest() != _pino():
        raise CertificadoNaoConfere(
            'O servidor de e-mail apresentou um certificado diferente do esperado.')
