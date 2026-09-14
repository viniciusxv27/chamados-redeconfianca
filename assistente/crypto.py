"""Cifra a chave da Anthropic em repouso.

A chave é um segredo do usuário (billing dele). Guardar em texto puro no banco
seria descuido, então cifra com Fernet usando uma chave derivada do
``SECRET_KEY`` do portal. Não é cofre de hardware, mas tira a chave do alcance
de quem só olha o banco — e ela nunca volta para a tela.
"""
import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet():
    # SHA-256 do SECRET_KEY → 32 bytes → base64 urlsafe, formato que o Fernet pede.
    chave = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(chave)


def cifrar(texto: str) -> str:
    return _fernet().encrypt((texto or '').encode()).decode()


def decifrar(token: str) -> str:
    return _fernet().decrypt((token or '').encode()).decode()
