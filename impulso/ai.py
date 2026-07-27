"""Resumo do feedback do Impulso via OpenAI.

Reaproveita o padrão de feedback/ai.py: mesmo guard de OPENAI_API_KEY,
cliente openai.OpenAI, chat.completions com gpt-4o-mini, cache no objeto
(ai_summary / ai_summary_generated_at / ai_summary_error).
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _build_prompt(fb) -> str:
    colaborador = fb.colaborador.get_full_name() or fb.colaborador.username
    gestor = fb.gestor.get_full_name() or fb.gestor.username

    # Contexto de metas do mês (se houver), para enriquecer o resumo.
    from .utils import calcular_faixa
    dados = calcular_faixa(fb.colaborador)
    metas_linha = (
        f"Metas avaliadas: {dados['avaliadas']} (de {dados['total']}); "
        f"média qualidade: {dados['media_qualidade']}/5; "
        f"média prazo: {dados['media_prazo']}/5; "
        f"faixa atual: {dados['faixa']}."
        if dados['avaliadas'] else 'Sem metas avaliadas registradas até o momento.'
    )

    return f"""Você é um analista de RH. Gere uma ANÁLISE curta e fácil de ler, em português (máx. 250 palavras), sobre o feedback mensal abaixo.

Responda EXATAMENTE com 2 seções, nesta ordem e com estes títulos em markdown:

## Resumo
Um parágrafo executivo e direto sobre o desempenho do colaborador no mês.

## Pontos a Melhorar
Lista em bullets, objetiva e prática, dos principais pontos a desenvolver e recomendações acionáveis.

---
Dados do feedback:

Colaborador: {colaborador}
Gestor: {gestor}
Mês de referência: {fb.referencia_mes:%m/%Y}

Pontos fortes (texto do gestor): {fb.pontos_fortes or '-'}
Pontos a melhorar (texto do gestor): {fb.pontos_melhoria or '-'}
Comentário geral: {fb.comentario or '-'}

Contexto de metas: {metas_linha}
"""


def generate_feedback_summary(fb, force: bool = False) -> str:
    """Gera (ou retorna em cache) o resumo IA do feedback. Persiste no objeto."""
    if fb.ai_summary and not force:
        return fb.ai_summary

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    if not api_key:
        fb.ai_summary_error = 'OPENAI_API_KEY não configurada.'
        fb.save(update_fields=['ai_summary_error'])
        return ''

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        prompt = _build_prompt(fb)
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': 'Você é um analista de RH especializado em feedbacks.'},
                {'role': 'user', 'content': prompt},
            ],
            temperature=0.3,
            max_tokens=700,
        )
        text = (resp.choices[0].message.content or '').strip()
        fb.ai_summary = text
        fb.ai_summary_generated_at = timezone.now()
        fb.ai_summary_error = ''
        fb.save(update_fields=['ai_summary', 'ai_summary_generated_at', 'ai_summary_error'])
        return text
    except Exception as exc:  # pragma: no cover
        logger.exception('Erro gerando resumo IA do feedback Impulso %s', fb.pk)
        fb.ai_summary_error = str(exc)[:500]
        fb.save(update_fields=['ai_summary_error'])
        return ''
