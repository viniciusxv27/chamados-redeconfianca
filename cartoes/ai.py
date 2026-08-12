"""Análise de despesa de cartão de crédito via OpenAI (gpt-4o-mini, visão).

Aceita a FOTO de um comprovante (image_bytes) e/ou um texto digitado
manualmente (manual_text) e devolve os dados estruturados do gasto para o
usuário revisar antes de abrir o chamado. Reaproveita o padrão de
feedback/ai.py e impulso/ai.py (lazy import, guard de OPENAI_API_KEY,
gpt-4o-mini, try/except) e a extração de JSON de agenda/views.py.

Novo no repositório: envio de IMAGEM à OpenAI (content multimodal). Nenhum
código anterior fazia isso — não há conflito.
"""
from __future__ import annotations

import base64
import json
import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    'Você é um analista financeiro. A partir de um comprovante de despesa de '
    'cartão de crédito (foto e/ou texto), extraia os dados e gere uma descrição '
    'clara e completa para abertura de um chamado interno. Responda APENAS com '
    'JSON válido, sem markdown e sem cercas de código.'
)

_USER_INSTRUCTION = (
    'Retorne um objeto JSON com as chaves: '
    '"estabelecimento" (string), '
    '"valor" (número em reais, ex.: 123.45, use ponto decimal), '
    '"data" (data do gasto no formato YYYY-MM-DD, ou "" se não identificar), '
    '"categoria" (string curta, ex.: "Alimentação", "Combustível", "Hospedagem"), '
    '"descricao" (texto em português, 1 a 3 frases, pronto para o chamado), '
    '"confianca" ("alta", "media" ou "baixa"), '
    '"observacoes" (string com o que estiver ilegível ou faltando). '
    'Não invente valores: se algo não estiver claro, deixe vazio e registre em "observacoes".'
)


def _extract_json_payload(text: str) -> dict:
    """Tira cercas ``` e faz fallback via regex (igual a agenda/views.py)."""
    cleaned = (text or '').strip()
    if cleaned.startswith('```'):
        lines = cleaned.split('\n')
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        cleaned = '\n'.join(lines).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if not match:
            raise
        return json.loads(match.group(0))


def analyze_expense(image_bytes: bytes | None = None, manual_text: str = '',
                    mime: str = 'image/jpeg') -> dict:
    """Analisa uma despesa (foto e/ou texto) e devolve dados estruturados.

    Sempre devolve um dict. Em caso de chave ausente ou erro, inclui a chave
    ``error`` (o chamador degrada para preenchimento manual).
    """
    if not image_bytes and not (manual_text or '').strip():
        return {'error': 'Envie a foto do comprovante ou descreva o gasto.'}

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    if not api_key:
        return {'error': 'OPENAI_API_KEY não configurada.'}

    user_content: list = [{'type': 'text', 'text': _USER_INSTRUCTION}]
    if manual_text and manual_text.strip():
        user_content.append({'type': 'text', 'text': f'Texto informado pelo usuário:\n{manual_text.strip()}'})
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode('ascii')
        safe_mime = mime if mime in ('image/jpeg', 'image/png', 'image/webp', 'image/gif') else 'image/jpeg'
        user_content.append({
            'type': 'image_url',
            'image_url': {
                'url': f'data:{safe_mime};base64,{b64}',
                'detail': 'high',  # 'high' para ler valores/CNPJ pequenos no comprovante
            },
        })

    try:
        import openai
        client = openai.OpenAI(api_key=api_key, timeout=60)
        resp = client.chat.completions.create(
            model='gpt-4o-mini',  # vision-capable, mesmo modelo dos resumos do projeto
            messages=[
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                {'role': 'user', 'content': user_content},
            ],
            temperature=0.2,
            max_tokens=800,
            response_format={'type': 'json_object'},
        )
        content = (resp.choices[0].message.content or '').strip()
        data = _extract_json_payload(content)
        # Normaliza tipos que a UI espera.
        data.setdefault('estabelecimento', '')
        data.setdefault('categoria', '')
        data.setdefault('data', '')
        data.setdefault('descricao', '')
        return data
    except Exception as exc:  # noqa: BLE001 - degrada para manual, nunca quebra a tela
        logger.exception('Erro analisando despesa de cartão via IA')
        return {'error': str(exc)[:500]}
