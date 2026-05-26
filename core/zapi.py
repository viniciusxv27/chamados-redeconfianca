"""Pequeno cliente HTTP para a Z-API (envio de WhatsApp).

Documentação: https://developer.z-api.io/

Uso típico:

    from core.zapi import send_whatsapp_message
    send_whatsapp_message('+5588999998888', 'Olá!')

A função retorna ``(ok: bool, detail: str)``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Tuple
from urllib import request as urlrequest, error as urlerror

from django.conf import settings

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
    """Mantém apenas dígitos. Adiciona DDI 55 (Brasil) quando ausente.

    A Z-API aceita o telefone no formato internacional sem '+' (ex: 5588999998888).
    """
    if not phone:
        return ''
    digits = re.sub(r'\D', '', str(phone))
    if not digits:
        return ''
    if not digits.startswith('55'):
        digits = '55' + digits
    return digits


def send_whatsapp_message(phone: str, message: str, *, timeout: int = 10) -> Tuple[bool, str]:
    """Envia uma mensagem de texto via Z-API.

    Retorna ``(ok, detail)`` para que o chamador decida como reportar o erro.
    Nunca lança exceção — em caso de falha de rede registra log e devolve
    ``(False, '...mensagem de erro...')``.
    """
    instance_id = getattr(settings, 'ZAPI_INSTANCE_ID', '')
    token = getattr(settings, 'ZAPI_TOKEN', '')
    client_token = getattr(settings, 'ZAPI_CLIENT_TOKEN', '')
    base_url = getattr(settings, 'ZAPI_BASE_URL', 'https://api.z-api.io').rstrip('/')

    if not (instance_id and token):
        return False, 'Z-API não configurada (ZAPI_INSTANCE_ID/ZAPI_TOKEN ausentes).'

    normalized = _normalize_phone(phone)
    if not normalized:
        return False, 'Telefone inválido.'

    url = f"{base_url}/instances/{instance_id}/token/{token}/send-text"
    payload = json.dumps({'phone': normalized, 'message': message}).encode('utf-8')

    headers = {'Content-Type': 'application/json'}
    if client_token:
        headers['Client-Token'] = client_token

    req = urlrequest.Request(url, data=payload, method='POST', headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', errors='replace')
            if 200 <= resp.status < 300:
                return True, body
            logger.warning('Z-API retornou status %s: %s', resp.status, body)
            return False, f'HTTP {resp.status}: {body[:200]}'
    except urlerror.HTTPError as exc:
        try:
            err_body = exc.read().decode('utf-8', errors='replace')
        except Exception:
            err_body = ''
        logger.warning('Z-API HTTPError %s: %s', exc.code, err_body)
        return False, f'HTTP {exc.code}: {err_body[:200]}'
    except urlerror.URLError as exc:
        logger.warning('Z-API URLError: %s', exc)
        return False, f'Falha de rede ao contatar Z-API: {exc.reason}'
    except Exception as exc:  # noqa: BLE001 — pragmatismo: nunca explodir
        logger.exception('Z-API erro inesperado')
        return False, f'Erro inesperado: {exc}'
