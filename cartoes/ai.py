"""Análise de despesa de cartão de crédito via OpenAI (visão + texto).

Recebe o comprovante — **imagem ou PDF** — e/ou o texto que a pessoa digitou, e
devolve os dados do gasto: valor, categoria e descrição (além de
estabelecimento e data). Enquanto faltar alguma dessas três coisas, insiste:
tenta de novo dizendo o que não veio, sobe para um modelo mais forte e, na
última rodada, pede o mínimo necessário. Uma resposta incompleta não é o fim —
é a entrada da tentativa seguinte.

PDF não passa pela API de visão como imagem: o texto sai com `pdfplumber` e as
páginas viram PNG com `pypdfium2` (as duas bibliotecas já estão no projeto,
em `experiencia/pdf_parser.py` e `apresentacoes/importacao.py`). Comprovante em
PDF costuma ter as duas coisas, e mandar texto **e** imagem é o que faz a IA
acertar o valor de um cupom mal escaneado.

Degrada com elegância: sem chave, ou depois de esgotar as tentativas, devolve o
que conseguiu com a chave ``error``/``faltou`` — quem chama registra o gasto
com "valor a confirmar" em vez de perder o lançamento.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time

from django.conf import settings

logger = logging.getLogger(__name__)

# Rodadas, na ordem. A primeira é a barata; se faltar dado, a insistência sobe
# de modelo. A última pede só o essencial — melhor um valor com categoria
# "Outros" do que um chamado vazio.
RODADAS = (
    {'modelo': 'gpt-4o-mini', 'temperatura': 0.2},
    {'modelo': 'gpt-4o-mini', 'temperatura': 0.0},
    {'modelo': 'gpt-4o', 'temperatura': 0.0},
    {'modelo': 'gpt-4o', 'temperatura': 0.0, 'essencial': True},
)
MODELO_RESERVA = 'gpt-4o-mini'          # quando o modelo pedido não existe na conta
ESPERA_ENTRE_RODADAS = 0.6              # segundos; erro passageiro costuma passar
PAGINAS_DO_PDF = 3                      # comprovante é curto; não vale mandar 40 páginas
TEXTO_MAXIMO = 8000

MIMES_DE_IMAGEM = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')


_SYSTEM_PROMPT = (
    'Você é um analista financeiro. A partir de um comprovante de despesa de '
    'cartão de crédito (foto, PDF e/ou texto), extraia os dados e gere uma descrição '
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

_REFORCO = (
    'A tentativa anterior não trouxe: {faltou}. Olhe o comprovante de novo com '
    'atenção — o valor total costuma ser o maior número com duas casas, perto de '
    '"TOTAL", "VALOR", "R$" ou do nome da bandeira. Se houver mais de um valor '
    '(subtotal, troco, parcelas), use o TOTAL PAGO.'
)

_ESSENCIAL = (
    'Última tentativa: o que importa é não deixar o chamado vazio. Preencha '
    '"valor" com o total pago (número, ponto decimal) mesmo que precise ler um '
    'número borrado — nesse caso diga em "observacoes" que a leitura é incerta. '
    'Preencha "categoria" com a melhor aproximação (use "Outros" se não der para '
    'saber) e "descricao" com o que dá para ver no comprovante. Só deixe "valor" '
    'vazio se realmente não houver nenhum número de dinheiro no material.'
)

# O que o chamado precisa ter. É isto que decide se vale insistir.
OBRIGATORIOS = ('valor', 'categoria', 'descricao')


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


def _modelo_inexistente(exc) -> bool:
    texto = str(exc)
    return (type(exc).__name__ == 'NotFoundError' or 'model_not_found' in texto
            or 'does not exist' in texto)


# ---------------------------------------------------------------------------
# O anexo: imagem, PDF ou nada
# ---------------------------------------------------------------------------
def e_pdf(conteudo: bytes | None, mime: str = '') -> bool:
    """PDF pelo conteúdo, não pelo rótulo — o mime que chega do WhatsApp mente."""
    if (mime or '').lower().startswith('application/pdf'):
        return True
    return bool(conteudo) and conteudo[:5] == b'%PDF-'


def texto_do_pdf(conteudo: bytes, limite: int = TEXTO_MAXIMO) -> str:
    """O texto do PDF, quando ele não é só imagem escaneada."""
    try:
        import pdfplumber
        partes = []
        with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
            for pagina in pdf.pages[:PAGINAS_DO_PDF]:
                partes.append(pagina.extract_text() or '')
                if sum(len(p) for p in partes) > limite:
                    break
        return '\n'.join(p for p in partes if p).strip()[:limite]
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Texto do PDF do comprovante não saiu: %s', exc)
        return ''


def paginas_do_pdf(conteudo: bytes, limite: int = PAGINAS_DO_PDF, largura: int = 1400) -> list[bytes]:
    """As páginas do PDF como PNG — o caminho do comprovante escaneado."""
    try:
        import pypdfium2 as pdfium
        documento = pdfium.PdfDocument(conteudo)
        paginas = []
        try:
            for indice in range(min(len(documento), limite)):
                pagina = documento[indice]
                largura_pt = pagina.get_width() or 960
                imagem = pagina.render(scale=largura / largura_pt).to_pil()
                saida = io.BytesIO()
                imagem.convert('RGB').save(saida, 'PNG')
                paginas.append(saida.getvalue())
        finally:
            documento.close()
        return paginas
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Páginas do PDF do comprovante não renderizaram: %s', exc)
        return []


def _imagem_aceita(conteudo: bytes, mime: str) -> tuple[bytes, str] | None:
    """A imagem como a API de visão aceita — convertendo o formato exótico."""
    if (mime or '').lower() in MIMES_DE_IMAGEM:
        return conteudo, mime.lower()
    try:
        from PIL import Image
        imagem = Image.open(io.BytesIO(conteudo))
        saida = io.BytesIO()
        imagem.convert('RGB').save(saida, 'PNG')
        return saida.getvalue(), 'image/png'
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Comprovante em formato que não dá para ler (%s): %s', mime, exc)
        return None


def preparar_anexo(conteudo: bytes | None, mime: str = '') -> dict:
    """Traduz o que chegou para o que a IA consegue ler.

    Devolve ``{'tipo', 'imagens': [(bytes, mime)], 'texto': str}``. PDF vira
    texto **e** páginas em PNG: o texto acerta o valor quando existe, e a
    imagem salva o comprovante que é só um escaneado.
    """
    vazio = {'tipo': '', 'imagens': [], 'texto': ''}
    if not conteudo:
        return vazio

    if e_pdf(conteudo, mime):
        paginas = paginas_do_pdf(conteudo)
        return {'tipo': 'pdf',
                'imagens': [(pagina, 'image/png') for pagina in paginas],
                'texto': texto_do_pdf(conteudo)}

    imagem = _imagem_aceita(conteudo, mime or 'image/jpeg')
    if not imagem:
        return vazio
    return {'tipo': 'imagem', 'imagens': [imagem], 'texto': ''}


# ---------------------------------------------------------------------------
# A conversa com a IA
# ---------------------------------------------------------------------------
def _valor_valido(bruto) -> bool:
    if bruto in (None, '', False):
        return False
    try:
        texto = str(bruto).replace('R$', '').strip()
        # Aceita tanto 1234.56 quanto 1.234,56.
        if ',' in texto and '.' in texto:
            texto = texto.replace('.', '').replace(',', '.')
        elif ',' in texto:
            texto = texto.replace(',', '.')
        return float(texto) > 0
    except (TypeError, ValueError):
        return False


def faltando(dados: dict) -> list:
    """O que ainda não foi identificado — é o que faz valer outra tentativa."""
    if not isinstance(dados, dict) or dados.get('error'):
        return list(OBRIGATORIOS)
    falta = []
    if not _valor_valido(dados.get('valor')):
        falta.append('valor')
    if not str(dados.get('categoria') or '').strip():
        falta.append('categoria')
    if not str(dados.get('descricao') or '').strip():
        falta.append('descricao')
    return falta


def _mensagem_do_usuario(anexo: dict, manual_text: str, falta: list, essencial: bool) -> list:
    partes: list = [{'type': 'text', 'text': _USER_INSTRUCTION}]
    if falta:
        partes.append({'type': 'text', 'text': _REFORCO.format(faltou=', '.join(falta))})
    if essencial:
        partes.append({'type': 'text', 'text': _ESSENCIAL})
    if manual_text and manual_text.strip():
        partes.append({'type': 'text',
                       'text': f'Texto informado pelo usuário:\n{manual_text.strip()}'})
    if anexo.get('texto'):
        partes.append({'type': 'text',
                       'text': f'Texto extraído do PDF do comprovante:\n{anexo["texto"]}'})
    for conteudo, mime in anexo.get('imagens') or []:
        b64 = base64.b64encode(conteudo).decode('ascii')
        partes.append({'type': 'image_url',
                       'image_url': {'url': f'data:{mime};base64,{b64}',
                                     # 'high' para ler valores pequenos no cupom
                                     'detail': 'high'}})
    return partes


def _uma_rodada(cliente, rodada: dict, anexo: dict, manual_text: str, falta: list) -> dict:
    mensagens = [
        {'role': 'system', 'content': _SYSTEM_PROMPT},
        {'role': 'user', 'content': _mensagem_do_usuario(
            anexo, manual_text, falta, rodada.get('essencial', False))},
    ]

    def pedir(modelo):
        return cliente.chat.completions.create(
            model=modelo, messages=mensagens,
            temperature=rodada.get('temperatura', 0.2),
            max_tokens=800, response_format={'type': 'json_object'})

    try:
        resposta = pedir(rodada['modelo'])
    except Exception as exc:                                    # noqa: BLE001
        if rodada['modelo'] != MODELO_RESERVA and _modelo_inexistente(exc):
            logger.warning('Modelo %s indisponível; usando %s', rodada['modelo'], MODELO_RESERVA)
            resposta = pedir(MODELO_RESERVA)
        else:
            raise
    dados = _extract_json_payload((resposta.choices[0].message.content or '').strip())
    if not isinstance(dados, dict):
        raise ValueError('a IA respondeu algo que não é um objeto JSON')
    return dados


def _mais_completo(atual: dict | None, novo: dict) -> dict:
    """Junta o que cada rodada trouxe: o que já veio não se perde na seguinte."""
    if not atual:
        return dict(novo)
    juntos = dict(atual)
    for chave, valor in novo.items():
        if chave == 'error':
            continue
        if str(valor or '').strip() and not str(juntos.get(chave) or '').strip():
            juntos[chave] = valor
    # O valor só é substituído por um valor de verdade.
    if not _valor_valido(juntos.get('valor')) and _valor_valido(novo.get('valor')):
        juntos['valor'] = novo['valor']
    return juntos


def analyze_expense(image_bytes: bytes | None = None, manual_text: str = '',
                    mime: str = 'image/jpeg', rodadas=RODADAS) -> dict:
    """Analisa uma despesa (imagem, PDF e/ou texto) e devolve dados estruturados.

    Insiste enquanto faltar valor, categoria ou descrição — e o que uma rodada
    achou é aproveitado pela seguinte. Sempre devolve um dict: com ``error``
    quando não deu para falar com a IA, e com ``faltou`` quando ela respondeu
    mas alguma coisa continuou sem identificar.
    """
    anexo = preparar_anexo(image_bytes, mime)
    tem_texto = bool((manual_text or '').strip()) or bool(anexo.get('texto'))
    if not anexo.get('imagens') and not tem_texto:
        return {'error': 'Envie a foto (ou o PDF) do comprovante ou descreva o gasto.'}

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    if not api_key:
        return {'error': 'OPENAI_API_KEY não configurada.'}

    try:
        import openai
        cliente = openai.OpenAI(api_key=api_key, timeout=60)
    except Exception as exc:                                    # noqa: BLE001
        logger.exception('Não deu para falar com a OpenAI')
        return {'error': str(exc)[:500]}

    melhor: dict | None = None
    falta = list(OBRIGATORIOS)
    ultimo_erro = ''
    tentativas = 0

    for indice, rodada in enumerate(rodadas):
        tentativas += 1
        try:
            dados = _uma_rodada(cliente, rodada, anexo, manual_text, falta if indice else [])
            melhor = _mais_completo(melhor, dados)
            ultimo_erro = ''
        except Exception as exc:                                # noqa: BLE001
            ultimo_erro = str(exc)[:500]
            logger.warning('Tentativa %d de ler o comprovante falhou (%s): %s',
                           tentativas, rodada['modelo'], ultimo_erro)

        falta = faltando(melhor or {})
        if not falta:
            break
        if indice < len(rodadas) - 1:
            time.sleep(ESPERA_ENTRE_RODADAS)

    if melhor is None:
        logger.error('A IA não conseguiu ler o comprovante em %d tentativa(s)', tentativas)
        return {'error': ultimo_erro or 'A IA não respondeu.', 'tentativas': tentativas}

    for chave in ('estabelecimento', 'categoria', 'data', 'descricao'):
        melhor.setdefault(chave, '')
    melhor['tentativas'] = tentativas
    melhor['anexo'] = anexo.get('tipo')
    if falta:
        melhor['faltou'] = falta
    return melhor
