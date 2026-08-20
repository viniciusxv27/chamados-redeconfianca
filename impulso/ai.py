"""Resumo e nota do feedback do Impulso via OpenAI.

Reaproveita o padrão de feedback/ai.py: mesmo guard de OPENAI_API_KEY,
cliente openai.OpenAI, chat.completions com gpt-4o-mini, cache no objeto
(ai_summary / ai_summary_generated_at / ai_summary_error).

Duas decisões que valem explicar:

* **Resposta em JSON.** Antes o modelo devolvia markdown e a tela lia o texto
  cru. Para extrair uma nota dali seria preciso adivinhar com regex; pedindo
  JSON (``response_format``), a nota vem num campo, validada, e o texto é
  montado aqui.
* **Retentativa.** Falha de rede e limite de taxa acontecem, e um feedback sem
  resumo não se conserta sozinho. Cada geração tenta várias vezes com espera
  crescente antes de desistir, e o erro fica gravado para a tela poder
  oferecer "tentar de novo".
"""
from __future__ import annotations

import json
import logging
import time

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

MODELO = 'gpt-4o-mini'
TENTATIVAS = 4
ESPERA_INICIAL = 1.5      # segundos; dobra a cada tentativa


def _contexto_de_metas(fb) -> str:
    """Resumo das metas do colaborador, para a IA não opinar no vácuo.

    Os números moram em ``detalhes['metas']`` — ler direto da raiz do dicionário
    levantava KeyError e derrubava a geração antes mesmo de chamar a API. Era
    por isso que nenhum resumo saía.
    """
    try:
        from .utils import calcular_faixa
        dados = calcular_faixa(fb.colaborador) or {}
    except Exception as exc:
        logger.warning('Contexto de metas indisponível para o feedback %s: %s', fb.pk, exc)
        return 'Contexto de metas indisponível.'

    metas = (dados.get('detalhes') or {}).get('metas') or {}
    if metas.get('sem_metas') or not metas.get('total'):
        return 'Sem metas registradas no período.'

    partes = [f"Metas no período: {metas.get('total')}",
              f"concluídas: {metas.get('concluidas', 0)}",
              f"avaliadas: {metas.get('avaliadas', 0)}"]
    if metas.get('media_qualidade') is not None:
        partes.append(f"média de qualidade: {metas['media_qualidade']}/5")
    if metas.get('media_prazo') is not None:
        partes.append(f"média de prazo: {metas['media_prazo']}/5")
    if dados.get('faixa'):
        partes.append(f"faixa atual: {dados['faixa']}")
    return '; '.join(partes) + '.'


def _build_prompt(fb) -> str:
    colaborador = fb.colaborador.get_full_name() or fb.colaborador.username
    gestor = fb.gestor.get_full_name() or fb.gestor.username

    return f"""Você é um analista de RH. Analise o feedback mensal abaixo e responda em JSON.

Responda SOMENTE com um objeto JSON com exatamente estas chaves:

{{
  "resumo": "um parágrafo executivo e direto sobre o desempenho do colaborador no mês (máx. 120 palavras)",
  "pontos_a_melhorar": ["item objetivo e acionável", "outro item", "..."],
  "nota": 7.5,
  "justificativa_nota": "uma frase curta explicando a nota"
}}

Regras para a nota:
- número de 0 a 10, com no máximo uma casa decimal;
- 0-4 desempenho abaixo do esperado; 5-6 parcial; 7-8 dentro do esperado; 9-10 acima;
- baseie-se no conteúdo escrito pelo gestor e no contexto de metas, não invente fatos;
- se as informações forem insuficientes para julgar, use 5 e diga isso na justificativa.

Escreva em português do Brasil.

---
Dados do feedback:

Colaborador: {colaborador}
Gestor: {gestor}
Mês de referência: {fb.referencia_mes:%m/%Y}

Pontos fortes (texto do gestor): {fb.pontos_fortes or '-'}
Pontos a melhorar (texto do gestor): {fb.pontos_melhoria or '-'}
Comentário geral: {fb.comentario or '-'}

Contexto de metas: {_contexto_de_metas(fb)}
"""


def _nota_valida(valor):
    """Converte a nota da IA para 0-10, ou None se vier imprestável."""
    try:
        nota = round(float(valor), 1)
    except (TypeError, ValueError):
        return None
    if not 0 <= nota <= 10:
        return None
    return nota


def _texto_do_json(dados) -> str:
    """Monta o markdown que a tela já sabe exibir a partir do JSON."""
    partes = ['## Resumo', (dados.get('resumo') or '').strip() or '—', '', '## Pontos a Melhorar']
    itens = dados.get('pontos_a_melhorar') or []
    if isinstance(itens, str):
        itens = [itens]
    partes += [f'- {str(i).strip()}' for i in itens if str(i).strip()] or ['- —']
    if dados.get('justificativa_nota'):
        partes += ['', '## Sobre a nota', str(dados['justificativa_nota']).strip()]
    return '\n'.join(partes)


def _chamar_openai(prompt, api_key):
    import openai
    client = openai.OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=MODELO,
        messages=[
            {'role': 'system',
             'content': 'Você é um analista de RH especializado em feedbacks. '
                        'Responde sempre em JSON válido.'},
            {'role': 'user', 'content': prompt},
        ],
        temperature=0.3,
        max_tokens=800,
        response_format={'type': 'json_object'},
    )
    return json.loads(resp.choices[0].message.content or '{}')


def generate_feedback_summary(fb, force: bool = False, tentativas: int = TENTATIVAS) -> str:
    """Gera (ou devolve em cache) o resumo e a nota da IA. Persiste no objeto.

    Tenta ``tentativas`` vezes com espera crescente: falha de rede ou limite de
    taxa não deve deixar o feedback sem análise para sempre. O último erro fica
    em ``ai_summary_error`` para a tela poder mostrar e oferecer nova tentativa.
    """
    if fb.ai_summary and not force:
        return fb.ai_summary

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    if not api_key:
        fb.ai_summary_error = 'OPENAI_API_KEY não configurada.'
        fb.save(update_fields=['ai_summary_error'])
        return ''

    prompt = _build_prompt(fb)
    espera = ESPERA_INICIAL
    ultimo_erro = ''

    for tentativa in range(1, max(1, tentativas) + 1):
        try:
            dados = _chamar_openai(prompt, api_key)
            texto = _texto_do_json(dados)
            nota = _nota_valida(dados.get('nota'))

            fb.ai_summary = texto
            fb.nota_ia = nota
            fb.ai_summary_generated_at = timezone.now()
            fb.ai_summary_error = '' if nota is not None else (
                'A IA respondeu sem uma nota válida; o resumo foi mantido.')
            fb.ai_tentativas = (fb.ai_tentativas or 0) + tentativa
            fb.save(update_fields=['ai_summary', 'nota_ia', 'ai_summary_generated_at',
                                   'ai_summary_error', 'ai_tentativas'])
            return texto
        except Exception as exc:
            ultimo_erro = f'{type(exc).__name__}: {exc}'
            logger.warning('Resumo IA do feedback %s falhou (tentativa %d/%d): %s',
                           fb.pk, tentativa, tentativas, ultimo_erro)
            if tentativa < tentativas:
                time.sleep(espera)
                espera *= 2

    fb.ai_summary_error = ultimo_erro[:500]
    fb.ai_tentativas = (fb.ai_tentativas or 0) + tentativas
    fb.save(update_fields=['ai_summary_error', 'ai_tentativas'])
    return ''


def garantir_resumo(fb):
    """Gera o resumo se ainda não existe. Nunca levanta exceção.

    Serve para as telas: abrir um feedback sem análise dispara uma nova
    tentativa em vez de deixar o campo vazio para sempre.
    """
    if fb.ai_summary:
        return fb.ai_summary
    try:
        return generate_feedback_summary(fb)
    except Exception as exc:
        logger.warning('garantir_resumo falhou para o feedback %s: %s', fb.pk, exc)
        return ''
