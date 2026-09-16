"""Chamadas à OpenAI do Assistente de Apresentações.

Tudo passa por aqui — e os testes trocam `_cliente` e `_http` por dublês,
então nenhum teste chega à API de verdade.

- Texto e visão: chat.completions com JSON Schema estrito. Modelo configurável
  pelo SUPERADMIN; se o modelo escolhido não existir na conta, cai para o
  `MODELO_RESERVA` em vez de derrubar a geração.
- Imagem: images.generate (gpt-image-*), devolve os bytes do PNG.
- Voz: audio.speech (mp3).
- Vídeo: /v1/videos (Sora) por HTTP — o SDK instalado é anterior a essa API.
"""
import base64
import io
import json
import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)

MODELO_RESERVA = 'gpt-4o'
TIMEOUT = 240
LADO_MAXIMO_VISAO = 1600       # px: imagem maior só gasta token sem ajudar a leitura


class IAIndisponivel(Exception):
    """Sem chave, sem crédito ou erro que a pessoa precisa ver em português."""


def chave():
    return getattr(settings, 'OPENAI_API_KEY', '') or ''


def _cliente():
    if not chave():
        raise IAIndisponivel('A chave da OpenAI (OPENAI_API_KEY) não está configurada no portal.')
    import openai
    return openai.OpenAI(api_key=chave(), timeout=TIMEOUT, max_retries=2)


def _http():
    import httpx
    return httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0))


def mensagem_de_erro(exc):
    """Erro técnico → frase que a pessoa entende (sem vazar chave nem corpo da resposta)."""
    if isinstance(exc, IAIndisponivel):
        return str(exc)
    nome = type(exc).__name__
    texto = str(exc)
    if 'insufficient_quota' in texto or 'billing' in texto.lower():
        return 'A conta da OpenAI está sem crédito. Avise o responsável pelo portal.'
    if nome in ('AuthenticationError', 'PermissionDeniedError'):
        return 'A OpenAI recusou a chave do portal (sem permissão para este recurso).'
    if nome == 'RateLimitError':
        return 'A OpenAI está limitando pedidos agora. Tente de novo em alguns minutos.'
    if nome in ('APITimeoutError', 'ReadTimeout', 'ConnectTimeout', 'TimeoutException'):
        return 'A OpenAI demorou demais para responder. Tente de novo.'
    if nome in ('APIConnectionError', 'ConnectError'):
        return 'Não foi possível falar com a OpenAI (conexão). Tente de novo.'
    if 'content_policy' in texto or 'safety' in texto.lower() or 'moderation' in texto.lower():
        return 'A OpenAI recusou o pedido pelas regras de conteúdo. Reescreva o pedido.'
    if 'organization must be verified' in texto.lower():
        return 'A organização da OpenAI precisa ser verificada para usar este modelo (imagem/vídeo).'
    return 'A IA não conseguiu concluir o pedido. Tente de novo.'


def _modelo_de_raciocinio(modelo):
    return modelo.startswith(('gpt-5', 'o1', 'o3', 'o4'))


def _modelo_inexistente(exc):
    texto = str(exc)
    return type(exc).__name__ == 'NotFoundError' or 'model_not_found' in texto or 'does not exist' in texto


# ---------------------------------------------------------------------------
# Texto + visão
# ---------------------------------------------------------------------------
def imagem_em_data_url(conteudo, lado_maximo=LADO_MAXIMO_VISAO):
    """Bytes de imagem → data URL JPEG reduzida (o modelo lê bem e custa menos)."""
    from PIL import Image

    img = Image.open(io.BytesIO(conteudo))
    img.load()
    if img.mode not in ('RGB', 'L'):
        fundo = Image.new('RGB', img.size, (255, 255, 255))
        fundo.paste(img.convert('RGBA'), mask=img.convert('RGBA').split()[-1])
        img = fundo
    img = img.convert('RGB')
    img.thumbnail((lado_maximo, lado_maximo))
    saida = io.BytesIO()
    img.save(saida, 'JPEG', quality=85)
    return 'data:image/jpeg;base64,' + base64.b64encode(saida.getvalue()).decode()


def conteudo_com_imagens(texto, imagens):
    """Mensagem de usuário com texto e imagens (lista de (legenda, bytes))."""
    partes = [{'type': 'text', 'text': texto}]
    for legenda, dados in imagens:
        if legenda:
            partes.append({'type': 'text', 'text': legenda})
        try:
            partes.append({'type': 'image_url', 'image_url': {'url': imagem_em_data_url(dados), 'detail': 'high'}})
        except Exception as exc:                                # noqa: BLE001 — imagem ruim não derruba o pedido
            logger.warning('Imagem ignorada no pedido à IA: %s', exc)
    return partes


def chamar_json(mensagens, schema, nome_schema, modelo, max_tokens=16000):
    """Resposta do modelo validada pelo JSON Schema estrito. Devolve o dicionário."""
    cliente = _cliente()
    formato = {'type': 'json_schema', 'json_schema': {'name': nome_schema, 'strict': True, 'schema': schema}}

    def pedir(nome_modelo):
        parametros = {'model': nome_modelo, 'messages': mensagens, 'response_format': formato}
        if _modelo_de_raciocinio(nome_modelo):
            parametros['max_completion_tokens'] = max_tokens
        else:
            parametros['max_tokens'] = min(max_tokens, 16000)
            parametros['temperature'] = 0.6
        return cliente.chat.completions.create(**parametros)

    try:
        resposta = pedir(modelo)
    except Exception as exc:                                    # noqa: BLE001
        if modelo != MODELO_RESERVA and _modelo_inexistente(exc):
            logger.warning('Modelo %s indisponível; usando %s', modelo, MODELO_RESERVA)
            resposta = pedir(MODELO_RESERVA)
        else:
            raise
    escolha = resposta.choices[0]
    mensagem = escolha.message
    if getattr(mensagem, 'refusal', None):
        raise IAIndisponivel('A IA recusou o pedido: ' + str(mensagem.refusal)[:300])
    if escolha.finish_reason == 'length':
        raise IAIndisponivel('A resposta da IA ficou grande demais. Peça menos slides ou divida o pedido.')
    try:
        return json.loads(mensagem.content or '{}')
    except json.JSONDecodeError as exc:
        raise IAIndisponivel('A IA respondeu num formato inesperado. Tente de novo.') from exc


# ---------------------------------------------------------------------------
# Imagem, voz e vídeo
# ---------------------------------------------------------------------------
def gerar_imagem(prompt, modelo, tamanho='1536x1024', qualidade='medium'):
    """PNG (bytes) gerado pela IA."""
    cliente = _cliente()
    parametros = {'model': modelo, 'prompt': prompt[:3800], 'size': tamanho, 'n': 1}
    if modelo.startswith('gpt-image'):
        parametros['quality'] = qualidade
    else:                                    # dall-e-3 só devolve base64 se pedir
        parametros['response_format'] = 'b64_json'
        parametros['size'] = '1792x1024' if tamanho == '1536x1024' else tamanho
    resposta = cliente.images.generate(**parametros)
    dados = resposta.data[0]
    if getattr(dados, 'b64_json', None):
        return base64.b64decode(dados.b64_json)
    if getattr(dados, 'url', None):
        with _http() as http:
            r = http.get(dados.url)
            r.raise_for_status()
            return r.content
    raise IAIndisponivel('A IA não devolveu a imagem.')


def gerar_voz(texto, modelo, voz):
    """MP3 (bytes) com a narração do texto, em português do Brasil."""
    cliente = _cliente()
    parametros = {'model': modelo, 'voice': voz, 'input': texto[:4000], 'response_format': 'mp3'}
    if 'tts' in modelo and not modelo.startswith('tts-1'):
        parametros['extra_body'] = {'instructions': 'Fale em português do Brasil, com tom profissional, '
                                                    'acolhedor e ritmo de apresentação.'}
    resposta = cliente.audio.speech.create(**parametros)
    conteudo = getattr(resposta, 'content', None)
    if conteudo is None and hasattr(resposta, 'read'):
        conteudo = resposta.read()
    if not conteudo:
        raise IAIndisponivel('A IA não devolveu o áudio da narração.')
    return conteudo


def gerar_video(prompt, modelo, segundos='8', tamanho='1280x720', progresso=None, dormir=time.sleep,
                limite_segundos=900):
    """MP4 (bytes) gerado pelo Sora. `progresso(pct)` é chamado enquanto espera."""
    if not chave():
        raise IAIndisponivel('A chave da OpenAI (OPENAI_API_KEY) não está configurada no portal.')
    base = 'https://api.openai.com/v1/videos'
    cabecalho = {'Authorization': f'Bearer {chave()}'}
    with _http() as http:
        r = http.post(base, headers=cabecalho, files={
            'model': (None, modelo), 'prompt': (None, prompt[:4000]),
            'seconds': (None, str(segundos)), 'size': (None, tamanho)})
        if r.status_code >= 400:
            logger.warning('Sora recusou a criação do vídeo: %s %s', r.status_code, r.text[:300])
            raise IAIndisponivel(_erro_http_video(r))
        video = r.json()
        inicio = time.monotonic()
        while video.get('status') in ('queued', 'in_progress'):
            if time.monotonic() - inicio > limite_segundos:
                raise IAIndisponivel('O vídeo demorou demais para ficar pronto. Tente de novo mais tarde.')
            dormir(8)
            r = http.get(f"{base}/{video['id']}", headers=cabecalho)
            if r.status_code >= 400:
                raise IAIndisponivel(_erro_http_video(r))
            video = r.json()
            if progresso and video.get('progress') is not None:
                progresso(video.get('progress'))
        if video.get('status') != 'completed':
            erro = (video.get('error') or {}).get('message') or 'falhou'
            raise IAIndisponivel(f'O vídeo não foi gerado ({erro[:200]}).')
        r = http.get(f"{base}/{video['id']}/content", headers=cabecalho)
        if r.status_code >= 400:
            raise IAIndisponivel(_erro_http_video(r))
        return r.content


def _erro_http_video(resposta):
    try:
        dados = resposta.json().get('error') or {}
    except ValueError:
        dados = {}
    codigo = (dados.get('code') or '') + ' ' + (dados.get('message') or '')
    if resposta.status_code in (401, 403):
        return 'A conta da OpenAI não tem acesso à geração de vídeo (Sora).'
    if resposta.status_code == 429:
        return 'Limite de geração de vídeo atingido. Tente de novo mais tarde.'
    if 'moderation' in codigo.lower() or 'policy' in codigo.lower():
        return 'O pedido de vídeo foi recusado pelas regras de conteúdo. Reescreva a descrição.'
    return 'Não foi possível gerar o vídeo agora.'
