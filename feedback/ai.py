"""Integração com OpenAI para gerar resumo de feedbacks."""
from __future__ import annotations

import logging
import os
import tempfile

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _audio_block(feedback) -> str:
    """Monta o trecho do prompt referente ao áudio gravado (se houver)."""
    if not feedback.audio_transcription:
        return 'Áudio gravado: não foi anexado nenhum áudio a este feedback.'

    context_label = dict(feedback.AUDIO_CONTEXT_CHOICES).get(
        feedback.audio_context, 'Não informado'
    )
    return (
        f'Áudio gravado (contexto: {context_label}). '
        f'Transcrição do áudio:\n"""\n{feedback.audio_transcription.strip()}\n"""'
    )


def _build_prompt(feedback) -> str:
    scale_lines = []
    for field, label in feedback.SCALE_FIELDS:
        value = getattr(feedback, field)
        scale_lines.append(f'- {label}: {value if value is not None else "N/A"}/10')

    avg = feedback.average_score()
    avg_str = f'{avg}/10' if avg is not None else 'N/A'

    prev = feedback.previous_feedback()
    if prev:
        prev_avg = prev.average_score()
        prev_str = (
            f"Feedback anterior em {prev.data}: média {prev_avg if prev_avg is not None else 'N/A'}/10."
        )
    else:
        prev_str = 'Não há feedback anterior registrado para este colaborador.'

    has_audio = bool(feedback.audio_transcription)
    audio_instruction = (
        '\nNo "## Resumo", inclua também um parágrafo comentando o áudio gravado: '
        'descreva o tom da conversa, pontos relevantes ditos e como isso complementa '
        'ou contrasta com as notas e textos do formulário, considerando o contexto do áudio.'
        if has_audio else ''
    )

    return f"""Você é um analista de RH. Gere uma ANÁLISE estruturada em português (máx. 350 palavras) sobre o feedback abaixo.

Responda EXATAMENTE com 3 seções, nesta ordem e com estes títulos em markdown:

## Resumo
Um parágrafo executivo descrevendo o desempenho geral do colaborador, incluindo a evolução em relação ao feedback anterior (se houver).{audio_instruction}

## Pontos Fortes
Lista em bullets dos principais pontos fortes identificados.

## Pontos de Melhoria
Lista em bullets das principais oportunidades de melhoria e recomendações práticas.

---
Dados do feedback:

Colaborador avaliado: {feedback.evaluatee.get_full_name() or feedback.evaluatee.username}
Avaliador: {feedback.evaluator.get_full_name() or feedback.evaluator.username}
Setor/Área: {feedback.setor_area}
Data: {feedback.data}
Gestor imediato: {feedback.gestor_imediato}
Gestor mediato: {feedback.gestor_mediato}

Notas (0-10):
{chr(10).join(scale_lines)}
Média geral: {avg_str}
{prev_str}

Pontos Fortes (texto do avaliador): {feedback.pontos_fortes or '-'}
Oportunidades de Melhoria (texto do avaliador): {feedback.oportunidades_melhoria or '-'}
Ações Propostas: {feedback.acoes_propostas or '-'}

Auto-percepção - comunicação clara: {feedback.comunicacao_clara_texto or '-'}
Cumpriu metas (justificativa): {feedback.cumpriu_metas_texto or '-'}
Avaliação do suporte/orientação dos superiores: {feedback.suporte_orientacao_texto or '-'}

Notas sobre evolução: {feedback.evolution_notes or '-'}

{_audio_block(feedback)}
"""


def transcribe_feedback_audio(feedback, force: bool = False) -> str:
    """Transcreve o áudio anexado ao feedback usando o Whisper. Persiste no objeto."""
    if feedback.audio_transcription and not force:
        return feedback.audio_transcription

    if not feedback.audio_file:
        return ''

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    if not api_key:
        feedback.audio_transcription_error = 'OPENAI_API_KEY não configurada.'
        feedback.save(update_fields=['audio_transcription_error'])
        return ''

    temp_path = None
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)

        # Copia o arquivo do storage (local/S3) para um arquivo temporário local.
        original_name = os.path.basename(getattr(feedback.audio_file, 'name', '') or 'audio.webm')
        _, ext = os.path.splitext(original_name)
        suffix = ext or '.webm'

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            feedback.audio_file.open('rb')
            try:
                for chunk in feedback.audio_file.chunks():
                    tmp.write(chunk)
            finally:
                feedback.audio_file.close()
            temp_path = tmp.name

        with open(temp_path, 'rb') as audio_stream:
            whisper_response = client.audio.transcriptions.create(
                model='whisper-1',
                file=audio_stream,
                language='pt',
                response_format='text',
            )

        text = (
            whisper_response
            if isinstance(whisper_response, str)
            else getattr(whisper_response, 'text', str(whisper_response))
        ).strip()

        feedback.audio_transcription = text
        feedback.audio_transcribed_at = timezone.now()
        feedback.audio_transcription_error = ''
        feedback.save(update_fields=[
            'audio_transcription', 'audio_transcribed_at', 'audio_transcription_error',
        ])
        return text
    except Exception as exc:  # pragma: no cover
        logger.exception('Erro transcrevendo áudio do feedback %s', feedback.pk)
        feedback.audio_transcription_error = str(exc)[:500]
        feedback.save(update_fields=['audio_transcription_error'])
        return ''
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def generate_ai_summary(feedback, force: bool = False) -> str:
    """Gera (ou retorna em cache) o resumo IA do feedback. Persiste no objeto."""
    if feedback.ai_summary and not force:
        return feedback.ai_summary

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    if not api_key:
        feedback.ai_summary_error = 'OPENAI_API_KEY não configurada.'
        feedback.save(update_fields=['ai_summary_error'])
        return ''

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        prompt = _build_prompt(feedback)
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': 'Você é um analista de RH especializado em feedbacks.'},
                {'role': 'user', 'content': prompt},
            ],
            temperature=0.3,
            max_tokens=900,
        )
        text = (resp.choices[0].message.content or '').strip()
        feedback.ai_summary = text
        feedback.ai_summary_generated_at = timezone.now()
        feedback.ai_summary_error = ''
        feedback.save(update_fields=['ai_summary', 'ai_summary_generated_at', 'ai_summary_error'])
        return text
    except Exception as exc:  # pragma: no cover
        logger.exception('Erro gerando resumo IA do feedback %s', feedback.pk)
        feedback.ai_summary_error = str(exc)[:500]
        feedback.save(update_fields=['ai_summary_error'])
        return ''
