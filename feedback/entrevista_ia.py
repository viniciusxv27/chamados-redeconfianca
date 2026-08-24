"""Leitura do vídeo da entrevista de desligamento pela IA.

O caminho é o mesmo já usado nas transcrições de reunião: o arquivo vai para o
Whisper (que aceita vídeo — o ffmpeg descarta a imagem antes) e a transcrição
vai para o modelo de texto, que devolve resumo, pontos e nota.

Duas decisões que valem registro:

* a nota sai na **mesma escala 1 a 5** das perguntas da entrevista. Nota em
  outra escala obrigaria a converter de cabeça toda vez que alguém quisesse
  comparar a leitura da IA com o que o entrevistador anotou;
* falha **não apaga** o que já foi obtido. Se a transcrição funcionou e o
  resumo falhou, a transcrição fica salva e só o resumo é refeito na próxima
  tentativa — reprocessar um vídeo de uma hora custa tempo e dinheiro.
"""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

MODELO_TEXTO = 'gpt-4o'
TENTATIVAS = 3

PROMPT = """Você é um analista de RH experiente. Recebeu a transcrição de uma
entrevista de desligamento feita com um colaborador que está saindo da empresa.

Responda APENAS com um JSON válido (sem markdown, sem crases) com estas chaves:

- "resumo": texto de 150 a 300 palavras, em português, descrevendo a conversa:
  o motivo da saída, como a pessoa avalia liderança, equipe, reconhecimento e
  estrutura, e o clima geral do relato. Escreva para quem não assistiu ao vídeo.
- "pontos_positivos": lista de 2 a 5 frases curtas com o que a pessoa elogiou.
- "pontos_atencao": lista de 2 a 6 frases curtas com problemas relatados.
- "temas": lista de 3 a 8 palavras-chave em minúsculo.
- "risco_saida": "alto", "medio" ou "baixo" — o quanto o motivo da saída indica
  um problema da empresa (alto) e não uma escolha pessoal do colaborador (baixo).
- "nota": número de 1 a 5, com até uma casa decimal, avaliando a experiência do
  colaborador na empresa segundo o que ele relatou. 1 = experiência muito ruim,
  5 = experiência excelente. Seja fiel ao relato, não seja gentil com a empresa.
- "justificativa_nota": uma ou duas frases explicando a nota.

Se a transcrição estiver vazia, truncada ou não parecer uma entrevista, diga
isso no "resumo", use listas vazias e devolva "nota": null."""


def _cliente():
    chave = getattr(settings, 'OPENAI_API_KEY', '')
    if not chave:
        raise RuntimeError('OPENAI_API_KEY não configurada no .env.')
    from openai import OpenAI
    return OpenAI(api_key=chave)


def _nota_valida(valor):
    """Aceita a nota só dentro de 1 a 5 — fora disso, é melhor não ter nota."""
    try:
        nota = round(float(valor), 1)
    except (TypeError, ValueError):
        return None
    return nota if 1.0 <= nota <= 5.0 else None


def _lista(valor, limite=8):
    if not isinstance(valor, list):
        return []
    return [str(x).strip() for x in valor if str(x).strip()][:limite]


def transcrever(recording):
    """Texto falado no vídeo. Reaproveita o pipeline das transcrições de reunião.

    O helper da agenda devolve ``(texto, duração)``; aqui só o texto interessa.
    """
    from agenda.views import _transcribe_audio_from_storage

    cliente = _cliente()
    resultado = _transcribe_audio_from_storage(cliente, recording.video)
    texto = resultado[0] if isinstance(resultado, (tuple, list)) else resultado
    return (texto or '').strip()


def resumir(texto):
    """Resumo + nota a partir da transcrição, com nova tentativa quando falha."""
    cliente = _cliente()
    trecho = texto[:120000]

    ultimo_erro = None
    for tentativa in range(TENTATIVAS):
        try:
            resposta = cliente.chat.completions.create(
                model=MODELO_TEXTO,
                messages=[
                    {'role': 'system', 'content': PROMPT},
                    {'role': 'user', 'content': f'Transcrição da entrevista:\n\n{trecho}'},
                ],
                temperature=0.2,
                max_tokens=2000,
                response_format={'type': 'json_object'},
            )
            dados = json.loads((resposta.choices[0].message.content or '').strip())
            resumo = str(dados.get('resumo') or '').strip()
            if not resumo:
                raise ValueError('a IA respondeu sem resumo')
            return {
                'resumo': resumo,
                'pontos_positivos': _lista(dados.get('pontos_positivos'), 5),
                'pontos_atencao': _lista(dados.get('pontos_atencao'), 6),
                'temas': _lista(dados.get('temas'), 8),
                'risco_saida': str(dados.get('risco_saida') or '').lower().strip(),
                'nota': _nota_valida(dados.get('nota')),
                'justificativa_nota': str(dados.get('justificativa_nota') or '').strip(),
            }
        except Exception as erro:   # noqa: BLE001 — qualquer falha merece nova tentativa
            ultimo_erro = erro
            logger.warning('Resumo da entrevista falhou (tentativa %s/%s): %s',
                           tentativa + 1, TENTATIVAS, erro)

    raise RuntimeError(f'A IA não conseguiu resumir a entrevista: {ultimo_erro}')


def processar(recording_id):
    """Transcreve e resume uma gravação. Roda fora do ciclo da requisição."""
    from django.db import close_old_connections

    from .models import ExitInterviewRecording

    close_old_connections()
    recording = ExitInterviewRecording.objects.filter(pk=recording_id).first()
    if recording is None:
        return

    recording.status = ExitInterviewRecording.Status.PROCESSING
    recording.attempts += 1
    recording.error = ''
    recording.save(update_fields=['status', 'attempts', 'error'])

    try:
        # A transcrição só é refeita se ainda não temos nenhuma: é a parte cara.
        if not recording.transcription:
            recording.transcription = transcrever(recording)
            recording.save(update_fields=['transcription'])

        if not recording.transcription:
            raise RuntimeError('Não foi possível extrair áudio do vídeo enviado.')

        analise = resumir(recording.transcription)
        recording.summary = analise['resumo']
        recording.highlights = {
            'pontos_positivos': analise['pontos_positivos'],
            'pontos_atencao': analise['pontos_atencao'],
            'temas': analise['temas'],
            'risco_saida': analise['risco_saida'],
        }
        recording.score = analise['nota']
        recording.score_reason = analise['justificativa_nota']
        recording.status = ExitInterviewRecording.Status.DONE
        recording.processed_at = timezone.now()
        recording.save()
    except Exception as erro:   # noqa: BLE001
        logger.exception('Falha ao analisar entrevista %s', recording_id)
        recording.status = ExitInterviewRecording.Status.ERROR
        recording.error = str(erro)[:2000]
        recording.save(update_fields=['status', 'error'])
    finally:
        close_old_connections()


def processar_em_segundo_plano(recording_id):
    """Dispara o processamento sem travar quem enviou o vídeo."""
    import threading

    threading.Thread(target=processar, args=(recording_id,), daemon=True).start()
