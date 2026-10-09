"""Leitura do arquivo do PIS/Cofins: o formulário já abre preenchido.

Quem lança sobe a nota, o boleto ou o recibo e confere — não digita. Três
caminhos, do mais certo para o mais caro:

1. **XML** (NF-e, NFC-e, NFS-e): os campos estão marcados no próprio arquivo;
   lê direto, sem IA.
2. **PDF ou foto**: texto do PDF + imagem das páginas vão para a IA (o mesmo
   preparo do comprovante dos cartões, ``cartoes/ai.py``), pedindo o
   fornecedor separado de quem pagou.
3. **Linha digitável** do boleto: se aparecer no texto, o valor sai dela (os 10
   últimos dígitos) — confere ou completa o que a IA leu.

O cuidado principal é o CNPJ: documento de compra traz dois — o do fornecedor
(emitente, prestador, beneficiário, locador) e o nosso (destinatário, tomador,
pagador, locatário). A IA devolve os dois separados, e um CNPJ que é da própria
rede nunca vira fornecedor.

Devolve sempre um dict ``{'campos': {...}, 'fonte': ..., 'avisos': [...]}``;
``campos`` só tem o que foi lido com segurança. Erro de leitura não impede o
lançamento: a pessoa preenche à mão.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date

from django.conf import settings

from . import cnpj as receita

logger = logging.getLogger(__name__)

TIPOS = ('NF', 'BOLETO', 'ALUGUEL', 'OUTRO')
MODELO = 'gpt-4o-mini'
MODELO_FORTE = 'gpt-4o'

_SYSTEM = (
    'Você lê documentos de contas a pagar de uma empresa (nota fiscal de produto ou serviço, '
    'boleto, recibo de aluguel) para a apuração de PIS/Cofins. Responda APENAS com JSON válido.'
)
_INSTRUCAO = (
    'Extraia do documento e devolva um objeto JSON com as chaves:\n'
    '"tipo": "NF" (nota fiscal, DANFE, NFS-e, cupom fiscal), "BOLETO", "ALUGUEL" (recibo de aluguel) '
    'ou "OUTRO";\n'
    '"fornecedor_cnpj": CNPJ de QUEM RECEBE o dinheiro — emitente/prestador da nota, '
    'beneficiário/cedente do boleto, locador do aluguel (só os 14 dígitos, ou "");\n'
    '"fornecedor_nome": razão social de quem recebe;\n'
    '"pagador_cnpj": CNPJ de QUEM PAGA — destinatário/tomador da nota, pagador/sacado do boleto, '
    'locatário (14 dígitos, ou "");\n'
    '"numero": número da nota / do documento / nosso número do boleto (ou "");\n'
    '"data_emissao": data de emissão no formato YYYY-MM-DD (ou "");\n'
    '"vencimento": vencimento YYYY-MM-DD, se houver (ou "");\n'
    '"competencia": mês de competência/referência no formato YYYY-MM, quando o documento disser '
    '(ex.: "aluguel referente a setembro/2026"); senão "";\n'
    '"valor": valor total do documento em reais, número com ponto decimal (valor total da nota, '
    'valor do documento no boleto, valor do aluguel recebido);\n'
    '"descricao": o que foi comprado ou pago, curto (até 80 caracteres);\n'
    '"observacoes": o que estiver ilegível ou incerto.\n'
    'Não invente: se algo não estiver no documento, deixe "".'
)
_REFORCO = ('A leitura anterior não trouxe {faltou}. Leia de novo com atenção: o valor total costuma estar '
            'perto de "VALOR TOTAL", "TOTAL DA NOTA", "VALOR DO DOCUMENTO" ou "R$"; o CNPJ do fornecedor fica '
            'no cabeçalho, junto do nome de quem emitiu ou do beneficiário.')


# ── Utilidades ──────────────────────────────────────────────────────────────
def cnpjs_da_rede():
    """CNPJs da própria empresa que o portal conhece — nunca são fornecedor."""
    nossos = set()
    try:
        from users.models import User
        for bruto in User.objects.exclude(branch_cnpj='').values_list('branch_cnpj', flat=True).distinct():
            nossos.add(receita.so_digitos(bruto))
    except Exception:  # noqa: BLE001
        pass
    try:
        from renova.models import ConfiguracaoRenova
        cfg = ConfiguracaoRenova.objects.first()
        if cfg:
            nossos.add(receita.so_digitos(cfg.contrato_cnpj))
            for dados in (cfg.contrato_lojas or {}).values():
                nossos.add(receita.so_digitos((dados or {}).get('cnpj')))
    except Exception:  # noqa: BLE001
        pass
    for bruto in getattr(settings, 'CNPJS_DA_REDE', []) or []:
        nossos.add(receita.so_digitos(bruto))
    try:
        from .sefaz import cnpjs_monitorados
        nossos.update(cnpjs_monitorados())
    except Exception:  # noqa: BLE001
        pass
    return {c for c in nossos if receita.valido(c)}


def cnpjs_no_texto(texto):
    """Todos os CNPJs válidos que aparecem no texto, na ordem."""
    achados = []
    for bruto in re.findall(r'\d{2}[.\s]?\d{3}[.\s]?\d{3}\s?/?\s?\d{4}\s?-?\s?\d{2}', texto or ''):
        d = receita.so_digitos(bruto)
        if receita.valido(d) and d not in achados:
            achados.append(d)
    return achados


def valor_da_linha_digitavel(texto):
    """Valor pela linha digitável.

    Boleto bancário: 47 dígitos, os 10 últimos são o valor. Conta de consumo
    (energia, água, telefone): 48 dígitos começando por 8, em 4 blocos de 11 +
    dígito verificador; tirando os verificadores, o valor fica nas posições 5 a
    15 — quando o 3º dígito diz que ali vai valor em reais (6 ou 8).
    """
    for bruto in re.findall(r'\d[\d. -]{44,64}\d', texto or ''):
        d = re.sub(r'\D', '', bruto)
        if len(d) == 47 and int(d[-10:]):
            return f'{int(d[-10:]) / 100:.2f}'
        if len(d) == 48 and d[0] == '8' and d[2] in '68':
            barras = ''.join(d[i:i + 11] for i in range(0, 48, 12))
            if int(barras[4:15]):
                return f'{int(barras[4:15]) / 100:.2f}'
    return ''


def _valor(bruto):
    texto = str(bruto or '').replace('R$', '').strip()
    if not texto:
        return ''
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    try:
        numero = float(texto)
    except ValueError:
        return ''
    return f'{numero:.2f}' if numero > 0 else ''


def _data(bruto):
    texto = str(bruto or '').strip()[:10]
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', texto) or None
    if not m:
        m2 = re.fullmatch(r'(\d{2})/(\d{2})/(\d{4})', texto)
        if not m2:
            return ''
        texto = f'{m2.group(3)}-{m2.group(2)}-{m2.group(1)}'
    try:
        data = date.fromisoformat(texto)
    except ValueError:
        return ''
    return texto if date(2000, 1, 1) <= data <= date.today() else ''


def _competencia(bruto):
    m = re.match(r'(\d{4})-(\d{2})', str(bruto or ''))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return ''
    return f'{m.group(1)}-{m.group(2)}'


# ── XML ─────────────────────────────────────────────────────────────────────
def _sem_namespace(raiz):
    for el in raiz.iter():
        if isinstance(el.tag, str) and '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    return raiz


def _primeiro(raiz, *caminhos):
    for caminho in caminhos:
        el = raiz.find(caminho)
        if el is not None and (el.text or '').strip():
            return el.text.strip()
    return ''


def ler_xml(conteudo):
    """NF-e/NFC-e (layout nacional) e NFS-e (ABRASF e nacional, os mais comuns)."""
    try:
        raiz = _sem_namespace(ET.fromstring(conteudo))
    except ET.ParseError:
        return None
    if raiz.find('.//infNFe') is not None:
        emissao = _primeiro(raiz, './/ide/dhEmi', './/ide/dEmi')
        return {
            'tipo': 'NF',
            'cnpj': _primeiro(raiz, './/emit/CNPJ'),
            'razao_social': _primeiro(raiz, './/emit/xNome'),
            'numero': _primeiro(raiz, './/ide/nNF'),
            'data_documento': _data(emissao),
            'valor': _valor(_primeiro(raiz, './/total/ICMSTot/vNF')),
            'descricao': _primeiro(raiz, './/det/prod/xProd')[:80],
        }
    # NFS-e: cada prefeitura tem um layout, mas as marcas abaixo cobrem ABRASF e o padrão nacional.
    prestador = _primeiro(raiz, './/PrestadorServico//Cnpj', './/Prestador//Cnpj', './/prest/CNPJ',
                          './/emit/CNPJ', './/IdentificacaoPrestador//Cnpj', './/CpfCnpjPrestador/Cnpj')
    valor = _primeiro(raiz, './/ValorLiquidoNfse', './/ValoresNfse/ValorLiquidoNfse', './/Valores/ValorServicos',
                      './/ValorServicos', './/vLiq', './/vServ')
    if prestador or valor:
        emissao = _primeiro(raiz, './/DataEmissao', './/dhEmi', './/dhProc', './/Competencia')
        return {
            'tipo': 'NF',
            'cnpj': prestador,
            'razao_social': _primeiro(raiz, './/PrestadorServico/RazaoSocial', './/Prestador/RazaoSocial',
                                      './/prest/xNome', './/emit/xNome', './/RazaoSocial'),
            'numero': _primeiro(raiz, './/InfNfse/Numero', './/Nfse//Numero', './/nNFSe', './/Numero'),
            'data_documento': _data(emissao),
            'competencia': _competencia(_primeiro(raiz, './/Competencia', './/dCompet')),
            'valor': _valor(valor),
            'descricao': _primeiro(raiz, './/Discriminacao', './/xDescServ')[:80],
        }
    return None


# ── IA (PDF e foto) ─────────────────────────────────────────────────────────
def _falta(dados):
    falta = []
    if not _valor(dados.get('valor')):
        falta.append('o valor')
    if not receita.valido(dados.get('fornecedor_cnpj')):
        falta.append('o CNPJ do fornecedor')
    return falta


def _perguntar(cliente, modelo, anexo, falta):
    import base64

    from cartoes.ai import _extract_json_payload

    partes = [{'type': 'text', 'text': _INSTRUCAO}]
    if falta:
        partes.append({'type': 'text', 'text': _REFORCO.format(faltou=' e '.join(falta))})
    if anexo.get('texto'):
        partes.append({'type': 'text', 'text': f'Texto extraído do PDF:\n{anexo["texto"]}'})
    for conteudo, mime in anexo.get('imagens') or []:
        partes.append({'type': 'image_url', 'image_url': {
            'url': f'data:{mime};base64,{base64.b64encode(conteudo).decode("ascii")}', 'detail': 'high'}})
    resposta = cliente.chat.completions.create(
        model=modelo, temperature=0, max_tokens=700, response_format={'type': 'json_object'},
        messages=[{'role': 'system', 'content': _SYSTEM}, {'role': 'user', 'content': partes}])
    dados = _extract_json_payload(resposta.choices[0].message.content or '')
    return dados if isinstance(dados, dict) else {}


def ler_com_ia(anexo):
    """Pergunta à IA; se faltar valor ou CNPJ, insiste uma vez com o modelo mais forte."""
    api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    if not api_key:
        return None, 'a leitura automática de PDF e foto não está configurada (OPENAI_API_KEY)'
    try:
        import openai
        cliente = openai.OpenAI(api_key=api_key, timeout=60)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:200]
    melhor, erro = {}, ''
    for modelo in (MODELO, MODELO_FORTE):
        try:
            dados = _perguntar(cliente, modelo, anexo, _falta(melhor) if melhor else [])
        except Exception as exc:  # noqa: BLE001
            erro = str(exc)[:200]
            logger.warning('Leitura do documento PIS/Cofins falhou (%s): %s', modelo, erro)
            continue
        for chave, valor in dados.items():
            if str(valor or '').strip() and not str(melhor.get(chave) or '').strip():
                melhor[chave] = valor
        if not _falta(melhor):
            break
    return (melhor or None), erro


# ── Entrada ─────────────────────────────────────────────────────────────────
def ler_documento(conteudo, nome='', mime=''):
    """Os campos do formulário lidos do arquivo. Ver o docstring do módulo."""
    avisos = []
    nome = (nome or '').lower()
    texto_bruto = conteudo[:200].lstrip() if conteudo else b''
    nossos = cnpjs_da_rede()

    if nome.endswith('.xml') or texto_bruto.startswith(b'<?xml') or texto_bruto.startswith(b'<'):
        lido = ler_xml(conteudo)
        if not lido:
            return {'campos': {}, 'fonte': 'xml', 'avisos': ['Não reconheci este XML como nota fiscal — preencha à mão.']}
        return _finalizar(lido, 'xml', avisos, nossos)

    from cartoes.ai import preparar_anexo
    anexo = preparar_anexo(conteudo, mime)
    if not anexo.get('imagens') and not anexo.get('texto'):
        return {'campos': {}, 'fonte': '', 'avisos': ['Não deu para ler este arquivo — preencha à mão.']}

    dados, erro = ler_com_ia(anexo)
    if not dados:
        return {'campos': {}, 'fonte': 'ia',
                'avisos': [f'A leitura automática não respondeu ({erro}) — preencha à mão.' if erro
                           else 'A leitura automática não respondeu — preencha à mão.']}

    fornecedor = receita.so_digitos(dados.get('fornecedor_cnpj'))
    pagador = receita.so_digitos(dados.get('pagador_cnpj'))
    no_texto = cnpjs_no_texto(anexo.get('texto'))
    # A IA trocou os lados, ou leu o nosso CNPJ como fornecedor: corrige pelo que se sabe.
    if fornecedor in nossos and pagador and pagador not in nossos:
        fornecedor, pagador = pagador, fornecedor
    if not receita.valido(fornecedor) or fornecedor in nossos:
        candidatos = [c for c in no_texto if c not in nossos and c != pagador]
        fornecedor = candidatos[0] if len(candidatos) == 1 else ''
        if not fornecedor:
            avisos.append('Não deu para saber com segurança qual é o CNPJ do fornecedor — confira.')

    lido = {
        'tipo': dados.get('tipo') if dados.get('tipo') in TIPOS else '',
        'cnpj': fornecedor,
        'razao_social': str(dados.get('fornecedor_nome') or '').strip()[:200],
        'numero': str(dados.get('numero') or '').strip()[:60],
        'data_documento': _data(dados.get('data_emissao')),
        'competencia': _competencia(dados.get('competencia')),
        'valor': _valor(dados.get('valor')),
        'descricao': str(dados.get('descricao') or '').strip()[:255],
    }
    da_linha = valor_da_linha_digitavel(anexo.get('texto'))
    if da_linha:
        if lido['valor'] and lido['valor'] != da_linha:
            avisos.append(f'O valor lido ({lido["valor"]}) difere da linha digitável do boleto ({da_linha}); '
                          'usei o da linha digitável.')
        lido['valor'] = da_linha
        lido['tipo'] = lido['tipo'] or 'BOLETO'
    if dados.get('observacoes'):
        avisos.append(f'Leitura: {str(dados["observacoes"])[:200]}')
    return _finalizar(lido, 'ia', avisos, nossos)


def _finalizar(lido, fonte, avisos, nossos):
    campos = {k: v for k, v in lido.items() if v}
    cnpj = receita.so_digitos(campos.get('cnpj'))
    if cnpj and (not receita.valido(cnpj) or cnpj in nossos):
        campos.pop('cnpj')
        avisos.append('O CNPJ lido é da própria empresa ou não é válido — informe o do fornecedor.')
    elif cnpj:
        campos['cnpj'] = cnpj
    # Sem competência no documento, vale o mês da emissão.
    if not campos.get('competencia') and campos.get('data_documento'):
        campos['competencia'] = campos['data_documento'][:7]
    return {'campos': campos, 'fonte': fonte, 'avisos': avisos}
