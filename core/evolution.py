"""Cliente HTTP para a Evolution API (envio de WhatsApp).

É o canal do bot dos cartões corporativos: a instância recebe as mensagens do
WhatsApp (pelo n8n) e responde por aqui. Configuração só no .env:
EVOLUTION_API_URL, EVOLUTION_API_KEY e EVOLUTION_INSTANCE.

    from core.evolution import enviar_texto
    ok, detalhe = enviar_texto('5527999998888@s.whatsapp.net', 'Olá!')

Mesmo contrato da Z-API (core/zapi.py): devolve ``(ok, detalhe)`` e nunca lança
exceção.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Tuple
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote

from django.conf import settings

logger = logging.getLogger(__name__)


def normalizar_numero(numero: str) -> str:
    """Destino no formato que a Evolution aceita.

    O JID que chega do webhook (``5527999998888@s.whatsapp.net``) vai como está.
    Telefone vira só dígitos, com o DDI 55 quando falta.
    """
    texto = str(numero or '').strip()
    if '@' in texto:
        return texto
    digitos = re.sub(r'\D', '', texto)
    if not digitos:
        return ''
    if not digitos.startswith('55'):
        digitos = '55' + digitos
    return digitos


def enviar_texto(numero: str, texto: str, *, timeout: int = 10) -> Tuple[bool, str]:
    """Envia uma mensagem de texto pela instância configurada."""
    # Teste nunca manda WhatsApp: o banco de dev tem telefone de gente de verdade.
    from core.utils import processo_de_teste
    if processo_de_teste():
        logger.warning('WhatsApp (Evolution) bloqueado: processo de teste.')
        return False, 'Envio bloqueado: processo de teste.'

    base = (getattr(settings, 'EVOLUTION_API_URL', '') or '').rstrip('/')
    chave = getattr(settings, 'EVOLUTION_API_KEY', '') or ''
    instancia = getattr(settings, 'EVOLUTION_INSTANCE', '') or ''
    if not (base and chave and instancia):
        return False, 'Evolution API não configurada (EVOLUTION_API_URL/EVOLUTION_API_KEY/EVOLUTION_INSTANCE).'

    destino = normalizar_numero(numero)
    if not destino:
        return False, 'Número inválido.'

    url = f'{base}/message/sendText/{quote(instancia, safe="")}'
    corpo = json.dumps({'number': destino, 'text': texto}).encode('utf-8')
    req = urlrequest.Request(url, data=corpo, method='POST',
                             headers={'Content-Type': 'application/json', 'apikey': chave})
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            resposta = resp.read().decode('utf-8', errors='replace')
            if 200 <= resp.status < 300:
                return True, resposta
            logger.warning('Evolution API retornou status %s: %s', resp.status, resposta[:300])
            return False, f'HTTP {resp.status}: {resposta[:200]}'
    except urlerror.HTTPError as exc:
        try:
            resposta = exc.read().decode('utf-8', errors='replace')
        except Exception:  # noqa: BLE001
            resposta = ''
        logger.warning('Evolution API HTTPError %s: %s', exc.code, resposta[:300])
        return False, f'HTTP {exc.code}: {resposta[:200]}'
    except urlerror.URLError as exc:
        logger.warning('Evolution API URLError: %s', exc)
        return False, f'Falha de rede ao contatar a Evolution API: {exc.reason}'
    except Exception as exc:  # noqa: BLE001 — nunca explodir no meio do fluxo
        logger.exception('Evolution API erro inesperado')
        return False, f'Erro inesperado: {exc}'
