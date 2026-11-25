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
    Telefone do cadastro passa por ``core.telefone.normalizar``: DDI 55 + DDD +
    número, com o nono dígito — ou '' quando o cadastro não tem um número
    confiável (sem DDD, DDD inexistente, dígitos demais...). Antes os dígitos
    iam crus e a mensagem saía para um número que não existe.
    """
    texto = str(numero or '').strip()
    if '@' in texto:
        return texto
    from core.telefone import normalizar
    return normalizar(texto)


NAO_CONFIGURADA = 'Evolution API não configurada (EVOLUTION_API_URL/EVOLUTION_API_KEY/EVOLUTION_INSTANCE).'


def _configuracao():
    return ((getattr(settings, 'EVOLUTION_API_URL', '') or '').rstrip('/'),
            getattr(settings, 'EVOLUTION_API_KEY', '') or '',
            getattr(settings, 'EVOLUTION_INSTANCE', '') or '')


def explicar_falha(resposta: str, instancia: str) -> str:
    """O que fazer quando a falha é da instância, e não do destino ('' quando não é).

    Em 19/11/2026 o WhatsApp da instância caiu ("Connection Closed") e em 24/11 a
    instância foi apagada no servidor ("instance does not exist"); os registros
    guardaram só o JSON cru e ninguém percebeu por dias.
    """
    texto = str(resposta or '').lower()
    nome = str(instancia or '')[:30]
    if 'instance does not exist' in texto:
        return (f'A instância "{nome}" não existe na Evolution: confira EVOLUTION_INSTANCE no .env '
                f'(manage.py conferir_evolution mostra as conectadas).')
    if 'connection closed' in texto:
        return (f'O WhatsApp da instância "{nome}" está desconectado: leia o QR Code de novo '
                f'no manager da Evolution.')
    return ''


def _ler(caminho: str, *, timeout: int = 15):
    """GET na Evolution (só leitura): ``(codigo_http, dados_ou_texto)``; levanta erro de rede."""
    base, chave, _ = _configuracao()
    req = urlrequest.Request(f'{base}{caminho}', method='GET', headers={'apikey': chave})
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            corpo, codigo = resp.read().decode('utf-8', errors='replace'), resp.status
    except urlerror.HTTPError as exc:
        try:
            corpo = exc.read().decode('utf-8', errors='replace')
        except Exception:  # noqa: BLE001
            corpo = ''
        codigo = exc.code
    try:
        return codigo, json.loads(corpo or 'null')
    except ValueError:
        return codigo, corpo


def estado_instancia(instancia: str = '', *, timeout: int = 15) -> dict:
    """Estado da instância (a configurada, sem nome): ``{'instancia', 'existe', 'estado', 'detalhe'}``.

    Só leitura (``instance/connectionState``). ``existe`` é None quando não deu
    para saber (sem configuração, rede); ``estado`` 'open' é a única que envia.
    """
    base, chave, configurada = _configuracao()
    instancia = instancia or configurada
    resultado = {'instancia': instancia, 'existe': None, 'estado': '', 'detalhe': ''}
    if not (base and chave and instancia):
        resultado['detalhe'] = NAO_CONFIGURADA
        return resultado
    try:
        codigo, dados = _ler(f'/instance/connectionState/{quote(instancia, safe="")}', timeout=timeout)
    except urlerror.URLError as exc:
        resultado['detalhe'] = f'Falha de rede ao contatar a Evolution API: {exc.reason}'
        return resultado
    except Exception as exc:  # noqa: BLE001
        resultado['detalhe'] = f'Erro inesperado: {exc}'
        return resultado
    if codigo == 404:
        resultado.update(existe=False, detalhe=explicar_falha(json.dumps(dados), instancia)
                         or f'HTTP 404: {str(dados)[:200]}')
    elif 200 <= codigo < 300 and isinstance(dados, dict):
        resultado.update(existe=True, estado=str((dados.get('instance') or dados).get('state') or ''))
    else:
        resultado['detalhe'] = f'HTTP {codigo}: {str(dados)[:200]}'
    return resultado


def listar_instancias(*, timeout: int = 15):
    """Instâncias do servidor: ``(ok, [{'nome', 'estado', 'numero', 'perfil', 'criada_em'}], detalhe)``.

    Só leitura (``instance/fetchInstances``). Nunca lança exceção.
    """
    base, chave, _ = _configuracao()
    if not (base and chave):
        return False, [], NAO_CONFIGURADA
    try:
        codigo, dados = _ler('/instance/fetchInstances', timeout=timeout)
    except urlerror.URLError as exc:
        return False, [], f'Falha de rede ao contatar a Evolution API: {exc.reason}'
    except Exception as exc:  # noqa: BLE001
        return False, [], f'Erro inesperado: {exc}'
    if not (200 <= codigo < 300 and isinstance(dados, list)):
        return False, [], f'HTTP {codigo}: {str(dados)[:200]}'
    instancias = [{'nome': str(i.get('name') or ''), 'estado': str(i.get('connectionStatus') or ''),
                   'numero': str(i.get('ownerJid') or '').split('@')[0],
                   'perfil': str(i.get('profileName') or ''), 'criada_em': str(i.get('createdAt') or '')}
                  for i in dados if isinstance(i, dict)]
    return True, instancias, ''


def listar_grupos(*, instancia: str = '', timeout: int = 15):
    """Grupos de que o número da instância participa: ``(ok, [{'id', 'nome', 'membros'}], detalhe)``.

    Só leitura (``group/fetchAllGroups``), da instância configurada ou da
    ``instancia`` pedida. Nunca lança exceção.
    """
    from core.utils import processo_de_teste
    if processo_de_teste():
        return False, [], 'Consulta bloqueada: processo de teste.'
    base, chave, configurada = _configuracao()
    instancia = instancia or configurada
    if not (base and chave and instancia):
        return False, [], NAO_CONFIGURADA
    url = f'{base}/group/fetchAllGroups/{quote(instancia, safe="")}?getParticipants=false'
    req = urlrequest.Request(url, method='GET', headers={'apikey': chave})
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            dados = json.loads(resp.read().decode('utf-8', errors='replace') or '[]')
    except urlerror.HTTPError as exc:
        return False, [], f'HTTP {exc.code} ao listar os grupos.'
    except urlerror.URLError as exc:
        return False, [], f'Falha de rede ao contatar a Evolution API: {exc.reason}'
    except Exception as exc:  # noqa: BLE001
        logger.exception('Evolution API: erro ao listar grupos')
        return False, [], f'Erro inesperado: {exc}'
    if isinstance(dados, dict):                  # algumas versões embrulham a lista
        dados = dados.get('groups') or dados.get('data') or []
    grupos = []
    for g in dados if isinstance(dados, list) else []:
        jid = str((g or {}).get('id') or '').strip()
        if jid.endswith('@g.us'):
            grupos.append({'id': jid, 'nome': str(g.get('subject') or jid).strip()[:200],
                           'membros': g.get('size') or len(g.get('participants') or []) or None})
    grupos.sort(key=lambda g: g['nome'].lower())
    return True, grupos, ''


def enviar_texto(numero: str, texto: str, *, timeout: int = 10) -> Tuple[bool, str]:
    """Envia uma mensagem de texto pela instância configurada."""
    # Teste nunca manda WhatsApp: o banco de dev tem telefone de gente de verdade.
    from core.utils import processo_de_teste
    if processo_de_teste():
        logger.warning('WhatsApp (Evolution) bloqueado: processo de teste.')
        return False, 'Envio bloqueado: processo de teste.'

    base, chave, instancia = _configuracao()
    if not (base and chave and instancia):
        return False, NAO_CONFIGURADA

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
        explicado = explicar_falha(resposta, instancia)
        if explicado:
            return False, f'{explicado} (HTTP {exc.code})'
        return False, f'HTTP {exc.code}: {resposta[:200]}'
    except urlerror.URLError as exc:
        logger.warning('Evolution API URLError: %s', exc)
        return False, f'Falha de rede ao contatar a Evolution API: {exc.reason}'
    except Exception as exc:  # noqa: BLE001 — nunca explodir no meio do fluxo
        logger.exception('Evolution API erro inesperado')
        return False, f'Erro inesperado: {exc}'
