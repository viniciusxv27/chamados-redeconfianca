"""Emissão de NF-e (modelo 55, leiaute 4.00) direto nos webservices da SEFAZ.

Caminho escolhido: **certificado A1 da empresa + webservices da SEFAZ**, sem
provedor no meio. O portal já fala com a SEFAZ com o mesmo certificado (busca
de notas pelo DistDFe e manifestação, em ``contagem_caixa``), já assina XML
(XMLDSig RSA-SHA1 + C14N, como o leiaute pede) e a nota não paga taxa por
emissão. O preço é nosso: notas técnicas novas da SEFAZ (como os grupos do
IBS/CBS da reforma tributária) entram aqui.

O que este módulo faz:

- calcula itens e totais (ICMS por CST/CSOSN, PIS e Cofins por alíquota,
  frete/seguro/outras rateados pelos itens);
- confere a nota antes de gastar uma ida à SEFAZ (CFOP × entrada/saída × UF,
  CST × regime, devolução com chave referenciada…);
- monta o XML, gera a chave (44 dígitos, DV módulo 11), assina e envia
  (NFeAutorizacao4 síncrono; se a SEFAZ responder "lote recebido", consulta o
  recibo depois — NFeRetAutorizacao4);
- consulta a situação pela chave (NFeConsultaProtocolo4), cancela (110111) e
  emite carta de correção (110110) — NFeRecepcaoEvento4;
- guarda o certificado A1 e a senha cifrados (Fernet) no banco: entram pela
  tela de configuração e nunca voltam para ela.

Trava: nada sai daqui com a configuração desligada (``ConfiguracaoNFe.ligado``
nasce False), e o ambiente nasce em homologação. Os testes passam um
``transporte`` falso; o transporte real só é usado quando nenhum é passado.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import os
import random
import re
import tempfile
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from lxml import etree

logger = logging.getLogger('financeiro.nfe')

NS = 'http://www.portalfiscal.inf.br/nfe'
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
C14N = 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'
VERSAO = '4.00'
VER_PROC = 'Portal Rede Confianca 1.0'
TIMEOUT = (10, 60)
ZERO = Decimal('0.00')
CENTAVO = Decimal('0.01')

CODIGOS_UF = {
    'RO': '11', 'AC': '12', 'AM': '13', 'RR': '14', 'PA': '15', 'AP': '16', 'TO': '17', 'MA': '21', 'PI': '22',
    'CE': '23', 'RN': '24', 'PB': '25', 'PE': '26', 'AL': '27', 'SE': '28', 'BA': '29', 'MG': '31', 'ES': '32',
    'RJ': '33', 'SP': '35', 'PR': '41', 'SC': '42', 'RS': '43', 'MS': '50', 'MT': '51', 'GO': '52', 'DF': '53',
}

# Quem autoriza a NF-e de cada UF. As lojas da rede estão no ES e no RJ (SVRS) e em SP.
SVRS = 'https://nfe.svrs.rs.gov.br/ws/'
SVRS_HOM = 'https://nfe-homologacao.svrs.rs.gov.br/ws/'
SP = 'https://nfe.fazenda.sp.gov.br/ws/'
SP_HOM = 'https://homologacao.nfe.fazenda.sp.gov.br/ws/'
SERVICOS = {
    #  serviço:      (namespace do WSDL, caminho na SVRS, caminho em SP)
    'autorizacao': ('NFeAutorizacao4', 'NfeAutorizacao/NFeAutorizacao4.asmx', 'nfeautorizacao4.asmx'),
    'retorno': ('NFeRetAutorizacao4', 'NfeRetAutorizacao/NFeRetAutorizacao4.asmx', 'nferetautorizacao4.asmx'),
    'consulta': ('NFeConsultaProtocolo4', 'NfeConsulta/NfeConsulta4.asmx', 'nfeconsultaprotocolo4.asmx'),
    'status': ('NFeStatusServico4', 'NfeStatusServico/NfeStatusServico4.asmx', 'nfestatusservico4.asmx'),
    'evento': ('NFeRecepcaoEvento4', 'recepcaoevento/recepcaoevento4.asmx', 'nferecepcaoevento4.asmx'),
}
ACOES_SOAP = {'autorizacao': 'nfeAutorizacaoLote', 'retorno': 'nfeRetAutorizacaoLote', 'consulta': 'nfeConsultaNF',
              'status': 'nfeStatusServicoNF', 'evento': 'nfeRecepcaoEvento'}
UFS_SVRS = {'AC', 'AL', 'AP', 'DF', 'ES', 'PB', 'RJ', 'RN', 'RO', 'RR', 'SC', 'SE', 'TO'}

AUTORIZADA = ('100', '150')
DENEGADA = ('110', '301', '302', '303')
EVENTO_REGISTRADO = ('135', '136', '155')
XNOME_HOMOLOGACAO = 'NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL'
CONDICAO_CCE = (
    'A Carta de Correcao e disciplinada pelo paragrafo 1o-A do art. 7o do Convenio S/N, de 15 de dezembro de 1970 '
    'e pode ser utilizada para regularizacao de erro ocorrido na emissao de documento fiscal, desde que o erro nao '
    'esteja relacionado com: I - as variaveis que determinam o valor do imposto tais como: base de calculo, '
    'aliquota, diferenca de preco, quantidade, valor da operacao ou da prestacao; II - a correcao de dados '
    'cadastrais que implique mudanca do remetente ou do destinatario; III - a data de emissao ou de saida.'
)

CST_NORMAL = {'00': 'Tributada integralmente', '20': 'Com redução da base de cálculo', '40': 'Isenta',
              '41': 'Não tributada', '50': 'Suspensão', '60': 'Cobrado antes por substituição tributária',
              '90': 'Outras'}
CSOSN = {'102': 'Simples sem permissão de crédito', '103': 'Simples, isenção por faixa de receita',
         '300': 'Imune', '400': 'Não tributada pelo Simples', '500': 'ICMS cobrado antes por ST',
         '900': 'Outros (com ICMS destacado, se houver)'}
CST_PIS_ALIQ = ('01', '02')
CST_PIS_NT = ('04', '05', '06', '07', '08', '09')
CST_PIS_OUTR = ('49', '50', '51', '52', '53', '54', '55', '56', '60', '61', '62', '63', '64', '65', '66', '67',
                '70', '71', '72', '73', '74', '75', '98', '99')
CST_PIS = CST_PIS_ALIQ + CST_PIS_NT + CST_PIS_OUTR
CFOP_DEVOLUCAO = {
    '1201', '1202', '1203', '1204', '1208', '1209', '1410', '1411', '1503', '1504', '1505', '1506', '1553', '1660',
    '1661', '1662', '1918', '1919', '2201', '2202', '2203', '2204', '2208', '2209', '2410', '2411', '2503', '2504',
    '2505', '2506', '2553', '2660', '2661', '2662', '2918', '2919', '3201', '3202', '3211', '3503', '3553',
    '5201', '5202', '5208', '5209', '5210', '5410', '5411', '5412', '5413', '5503', '5553', '5555', '5556', '5660',
    '5661', '5662', '5918', '5919', '6201', '6202', '6208', '6209', '6210', '6410', '6411', '6412', '6413', '6503',
    '6553', '6555', '6556', '6660', '6661', '6662', '6918', '6919', '7201', '7202', '7210', '7211', '7553', '7556',
}


class NfeInvalida(Exception):
    """A nota (ou o pedido) não pode seguir — a mensagem vai para a tela."""


class SefazErro(Exception):
    """A SEFAZ não respondeu, ou respondeu algo que não dá para ler."""


# ── Pequenos ajudantes ──────────────────────────────────────────────────────
def so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def texto(valor, tamanho=None):
    """Sem quebras, espaços duplos ou nas pontas (a SEFAZ recusa)."""
    limpo = ' '.join(str(valor or '').split())
    return limpo[:tamanho] if tamanho else limpo


def sem_acento(valor):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(valor or '')) if not unicodedata.combining(c))


def dec(valor, padrao=ZERO):
    if isinstance(valor, Decimal):
        return valor
    bruto = str(valor if valor is not None else '').strip().replace('R$', '').replace(' ', '').replace('%', '')
    if not bruto:
        return padrao
    if ',' in bruto:
        bruto = bruto.replace('.', '').replace(',', '.')
    try:
        return Decimal(bruto)
    except (InvalidOperation, ValueError):
        raise NfeInvalida(f'"{valor}" não é um número.')


def r2(valor):
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def f2(valor):
    return f'{r2(valor):.2f}'


def f4(valor):
    return f'{Decimal(valor).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP):.4f}'


def f_unit(valor):
    """vUnCom: até 10 casas; sai com 2 a 10, sem zeros inúteis à direita."""
    texto_v = f'{Decimal(valor).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP):.10f}'
    inteiro, frac = texto_v.split('.')
    frac = frac.rstrip('0')
    return f'{inteiro}.{frac.ljust(2, "0")}'


def dv_modulo11(numero):
    pesos, soma = [2, 3, 4, 5, 6, 7, 8, 9], 0
    for i, d in enumerate(reversed(numero)):
        soma += int(d) * pesos[i % 8]
    resto = soma % 11
    return '0' if resto < 2 else str(11 - resto)


def data_hora(momento):
    texto_d = timezone.localtime(momento).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%S%z')
    return texto_d[:-2] + ':' + texto_d[-2:]


def chave_valida(chave):
    chave = so_digitos(chave)
    return len(chave) == 44 and dv_modulo11(chave[:43]) == chave[43]


# ── Cifra e certificado ─────────────────────────────────────────────────────
def _fernet():
    from cryptography.fernet import Fernet
    chave = getattr(settings, 'NFE_CHAVE', '') or ''
    if chave:
        return Fernet(chave.encode())
    bruto = hashlib.sha256(b'nfe-certificado:' + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(bruto))


def cifrar(dados: bytes) -> str:
    return _fernet().encrypt(dados).decode()


def decifrar(token: str) -> bytes:
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt((token or '').encode())
    except (InvalidToken, ValueError):
        return b''


def abrir_pfx(pfx: bytes, senha: str):
    from cryptography.hazmat.primitives.serialization import pkcs12
    try:
        chave, cert, cadeia = pkcs12.load_key_and_certificates(pfx, (senha or '').encode())
    except Exception as exc:  # noqa: BLE001
        raise NfeInvalida('Não deu para abrir o certificado: confira o arquivo .pfx e a senha.') from exc
    if chave is None or cert is None:
        raise NfeInvalida('O arquivo não traz a chave privada do certificado (precisa ser o A1 .pfx/.p12).')
    return chave, cert, cadeia or []


def cnpj_do_certificado(cert):
    from cryptography.x509.oid import NameOID
    for atributo in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME):
        digitos = so_digitos(str(atributo.value).rsplit(':', 1)[-1])
        if len(digitos) == 14:
            return digitos
    return ''


def guardar_certificado(config, pfx: bytes, senha: str, usuario=None):
    """Confere o .pfx e a senha e guarda os dois cifrados. Devolve a config."""
    from cryptography.x509.oid import NameOID
    _chave, cert, _cadeia = abrir_pfx(pfx, senha)
    validade = cert.not_valid_after_utc
    if validade <= timezone.now():
        raise NfeInvalida(f'O certificado venceu em {timezone.localtime(validade):%d/%m/%Y}.')
    nomes = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    config.certificado_cifrado = cifrar(base64.b64encode(pfx))
    config.senha_cifrada = cifrar((senha or '').encode())
    config.certificado_titular = (str(nomes[0].value) if nomes else '')[:200]
    config.certificado_cnpj = cnpj_do_certificado(cert)
    config.certificado_validade = validade
    config.atualizado_por = usuario
    config.save()
    return config


def carregar_certificado(config):
    if not config.tem_certificado:
        raise NfeInvalida('Falta o certificado A1: suba o .pfx na configuração da NF-e.')
    pfx = base64.b64decode(decifrar(config.certificado_cifrado) or b'')
    senha = decifrar(config.senha_cifrada).decode('utf-8', errors='replace')
    if not pfx:
        raise NfeInvalida('O certificado guardado não abre mais (a chave de cifra mudou): suba o .pfx de novo.')
    return abrir_pfx(pfx, senha)


@contextlib.contextmanager
def _arquivos_pem(config):
    """O certificado em PEM, em arquivos temporários (o requests pede caminho)."""
    from cryptography.hazmat.primitives import serialization
    chave, cert, cadeia = carregar_certificado(config)
    pasta = tempfile.mkdtemp(prefix='nfe-')
    caminho_cert, caminho_chave = os.path.join(pasta, 'cert.pem'), os.path.join(pasta, 'chave.pem')
    try:
        with open(caminho_cert, 'wb') as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
            for extra in cadeia:
                f.write(extra.public_bytes(serialization.Encoding.PEM))
        descritor = os.open(caminho_chave, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(descritor, 'wb') as f:
            f.write(chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
        yield caminho_cert, caminho_chave
    finally:
        for caminho in (caminho_cert, caminho_chave):
            with contextlib.suppress(OSError):
                os.remove(caminho)
        with contextlib.suppress(OSError):
            os.rmdir(pasta)


# ── Cálculo ─────────────────────────────────────────────────────────────────
def _rateio(total, pesos):
    """Divide ``total`` proporcional aos pesos; a sobra de centavo vai no último."""
    total = r2(total)
    soma = sum(pesos)
    if not total or not soma:
        return [ZERO for _ in pesos]
    partes = [r2(total * p / soma) for p in pesos]
    partes[-1] += total - sum(partes)
    return partes


def rateios(nota, itens):
    pesos = [r2(i.quantidade * i.valor_unitario) for i in itens]
    return (_rateio(nota.valor_frete, pesos), _rateio(nota.valor_seguro, pesos), _rateio(nota.valor_outras, pesos))


def calcular(nota, itens):
    """Preenche os valores calculados dos itens e os totais da nota (não grava)."""
    fretes, seguros, outras = rateios(nota, itens)
    for item, frete, seguro, outra in zip(itens, fretes, seguros, outras):
        item.valor_total = r2(item.quantidade * item.valor_unitario)
        item.valor_desconto = r2(item.valor_desconto or 0)
        base = item.valor_total - item.valor_desconto + frete + seguro + outra
        item.base_icms = item.valor_icms = ZERO
        cst = item.cst_icms
        if cst in ('00', '20') or (cst in ('90', '900') and item.aliquota_icms > 0):
            reducao = item.reducao_bc_icms if cst in ('20', '90', '900') else ZERO
            item.base_icms = r2(base * (1 - reducao / 100))
            item.valor_icms = r2(item.base_icms * item.aliquota_icms / 100)
        base_pc = item.valor_total - item.valor_desconto
        item.valor_pis = r2(base_pc * item.aliquota_pis / 100) if item.cst_pis in CST_PIS_ALIQ + CST_PIS_OUTR else ZERO
        item.valor_cofins = (r2(base_pc * item.aliquota_cofins / 100)
                             if item.cst_cofins in CST_PIS_ALIQ + CST_PIS_OUTR else ZERO)
    nota.valor_produtos = sum((i.valor_total for i in itens), ZERO)
    nota.valor_desconto = sum((i.valor_desconto for i in itens), ZERO)
    nota.base_icms = sum((i.base_icms for i in itens), ZERO)
    nota.valor_icms = sum((i.valor_icms for i in itens), ZERO)
    nota.valor_pis = sum((i.valor_pis for i in itens), ZERO)
    nota.valor_cofins = sum((i.valor_cofins for i in itens), ZERO)
    nota.valor_total = (nota.valor_produtos - nota.valor_desconto + r2(nota.valor_frete)
                        + r2(nota.valor_seguro) + r2(nota.valor_outras))
    if nota.dest_ind_ie == '9':
        nota.consumidor_final = True   # não contribuinte é sempre consumidor final (rejeição 696)
    return nota


def destino(nota):
    """idDest: 1 interna, 2 interestadual, 3 exterior."""
    if nota.dest_uf == 'EX':
        return '3'
    return '1' if nota.dest_uf == nota.emitente.uf else '2'


def validar(nota, itens):
    """Lista de problemas que a SEFAZ recusaria (vazia = pode enviar)."""
    erros = []
    emit = nota.emitente
    if not emit.ativo:
        erros.append('O emitente está desativado na configuração.')
    if emit.uf not in CODIGOS_UF:
        erros.append(f'UF do emitente inválida: {emit.uf!r}.')
    if not texto(nota.natureza):
        erros.append('Informe a natureza da operação (ex.: "Devolução de compra para comercialização").')
    documento = so_digitos(nota.dest_documento)
    if len(documento) not in (11, 14):
        erros.append('O CNPJ/CPF do destinatário precisa ter 14 ou 11 números.')
    if len(texto(nota.dest_nome)) < 2:
        erros.append('Informe o nome do destinatário.')
    if nota.dest_uf != 'EX' and nota.dest_uf not in CODIGOS_UF:
        erros.append('Informe a UF do destinatário.')
    if len(so_digitos(nota.dest_codigo_municipio)) != 7:
        erros.append('O código IBGE do município do destinatário tem 7 números.')
    if not texto(nota.dest_logradouro) or not texto(nota.dest_bairro) or not texto(nota.dest_municipio):
        erros.append('Endereço do destinatário incompleto (logradouro, bairro e município).')
    if nota.dest_ind_ie == '1' and not so_digitos(nota.dest_ie):
        erros.append('Destinatário contribuinte do ICMS precisa da inscrição estadual.')
    if nota.dest_ind_ie == '1' and len(documento) == 11 and not so_digitos(nota.dest_ie):
        erros.append('Pessoa física com IE: informe a inscrição.')
    if nota.finalidade == '4':
        if not chave_valida(nota.chave_referenciada):
            erros.append('Devolução precisa da chave (44 números) da nota original, com o dígito certo.')
    elif nota.chave_referenciada and not chave_valida(nota.chave_referenciada):
        erros.append('A chave referenciada não confere (44 números e dígito verificador).')
    if not itens:
        erros.append('A nota precisa de pelo menos um item.')
    if len(itens) > 990:
        erros.append('A NF-e aceita até 990 itens.')

    id_dest = destino(nota) if emit.uf in CODIGOS_UF else '1'
    entrada = nota.tipo_operacao == '0'
    primeiros = {'1': ('1', '5'), '2': ('2', '6'), '3': ('3', '7')}[id_dest]
    esperado = primeiros[0] if entrada else primeiros[1]
    validos_icms = CSOSN if emit.simples else CST_NORMAL
    for item in itens:
        n = f'Item {item.ordem}'
        if not texto(item.codigo) or not texto(item.descricao):
            erros.append(f'{n}: informe código e descrição.')
        if not re.fullmatch(r'\d{8}', item.ncm or ''):
            erros.append(f'{n}: o NCM tem 8 números.')
        if item.cest and not re.fullmatch(r'\d{7}', item.cest):
            erros.append(f'{n}: o CEST tem 7 números.')
        if not re.fullmatch(r'\d{4}', item.cfop or ''):
            erros.append(f'{n}: o CFOP tem 4 números.')
        elif item.cfop[0] != esperado:
            tipo = 'entrada' if entrada else 'saída'
            onde = {'1': 'dentro do estado', '2': 'para outro estado', '3': 'com o exterior'}[id_dest]
            erros.append(f'{n}: CFOP {item.cfop} não serve para {tipo} {onde} (começa com {esperado}).')
        elif nota.finalidade == '4' and item.cfop not in CFOP_DEVOLUCAO:
            erros.append(f'{n}: nota de devolução pede CFOP de devolução (ex.: 5202, 6202, 1202, 5411).')
        if item.quantidade is None or item.quantidade <= 0:
            erros.append(f'{n}: a quantidade precisa ser maior que zero.')
        if item.valor_unitario is None or item.valor_unitario < 0:
            erros.append(f'{n}: valor unitário inválido.')
        if item.valor_desconto and item.valor_desconto > r2(item.quantidade * item.valor_unitario):
            erros.append(f'{n}: o desconto passa do valor do item.')
        if item.cst_icms not in validos_icms:
            regime = 'Simples Nacional (CSOSN)' if emit.simples else 'regime normal (CST)'
            erros.append(f'{n}: {item.cst_icms!r} não é um código do ICMS do {regime}: use '
                         + ', '.join(validos_icms) + '.')
        if item.origem not in '012345678' or len(item.origem) != 1:
            erros.append(f'{n}: origem da mercadoria vai de 0 a 8.')
        if item.cst_pis not in CST_PIS or item.cst_cofins not in CST_PIS:
            erros.append(f'{n}: CST do PIS/Cofins não aceito aqui (use 01, 02, 04 a 09, 49 a 75, 98 ou 99).')
    if (nota.tipo_operacao == '1' and nota.finalidade == '1' and id_dest == '2'
            and nota.dest_ind_ie == '9' and nota.valor_icms > 0):
        erros.append('Venda para consumidor não contribuinte de outro estado pede o DIFAL (grupo ICMSUFDest), '
                     'que esta tela ainda não calcula.')
    return erros


# ── Chave de acesso ─────────────────────────────────────────────────────────
def gerar_chave(nota, momento):
    emit = nota.emitente
    numero = f'{nota.numero:09d}'
    if not nota.codigo_numerico or nota.codigo_numerico == numero[1:]:
        cnf = f'{random.SystemRandom().randint(1, 99999999):08d}'
        while cnf == numero[1:]:
            cnf = f'{random.SystemRandom().randint(1, 99999999):08d}'
        nota.codigo_numerico = cnf
    base = (CODIGOS_UF[emit.uf] + timezone.localtime(momento).strftime('%y%m') + emit.cnpj + '55'
            + f'{nota.serie:03d}' + numero + '1' + nota.codigo_numerico)
    return base + dv_modulo11(base)


# ── XML ─────────────────────────────────────────────────────────────────────
def _sub(pai, tag, valor=None):
    el = etree.SubElement(pai, f'{{{NS}}}{tag}')
    if valor is not None:
        el.text = str(valor)
    return el


def _endereco(pai, tag, logradouro, numero, complemento, bairro, cmun, municipio, uf, cep, fone):
    end = _sub(pai, tag)
    _sub(end, 'xLgr', texto(logradouro, 60))
    _sub(end, 'nro', texto(numero, 60) or 'S/N')
    if texto(complemento):
        _sub(end, 'xCpl', texto(complemento, 60))
    _sub(end, 'xBairro', texto(bairro, 60))
    _sub(end, 'cMun', so_digitos(cmun) if uf != 'EX' else '9999999')
    _sub(end, 'xMun', texto(municipio, 60) if uf != 'EX' else 'EXTERIOR')
    _sub(end, 'UF', uf)
    if so_digitos(cep):
        _sub(end, 'CEP', so_digitos(cep).zfill(8)[:8])
    _sub(end, 'cPais', '1058')
    _sub(end, 'xPais', 'BRASIL')
    if 6 <= len(so_digitos(fone)) <= 14:
        _sub(end, 'fone', so_digitos(fone))
    return end


def _icms(imposto, item, simples):
    icms = _sub(imposto, 'ICMS')
    cst = item.cst_icms
    com_valor = item.base_icms > 0
    if simples:
        grupo = {'102': 'ICMSSN102', '103': 'ICMSSN102', '300': 'ICMSSN102', '400': 'ICMSSN102',
                 '500': 'ICMSSN500', '900': 'ICMSSN900'}[cst]
        g = _sub(icms, grupo)
        _sub(g, 'orig', item.origem)
        _sub(g, 'CSOSN', cst)
        if cst == '900' and com_valor:
            _sub(g, 'modBC', '3')
            _sub(g, 'vBC', f2(item.base_icms))
            if item.reducao_bc_icms > 0:
                _sub(g, 'pRedBC', f4(item.reducao_bc_icms))
            _sub(g, 'pICMS', f4(item.aliquota_icms))
            _sub(g, 'vICMS', f2(item.valor_icms))
        return
    grupo = {'00': 'ICMS00', '20': 'ICMS20', '40': 'ICMS40', '41': 'ICMS40', '50': 'ICMS40',
             '60': 'ICMS60', '90': 'ICMS90'}[cst]
    g = _sub(icms, grupo)
    _sub(g, 'orig', item.origem)
    _sub(g, 'CST', cst)
    if cst == '00':
        _sub(g, 'modBC', '3')
        _sub(g, 'vBC', f2(item.base_icms))
        _sub(g, 'pICMS', f4(item.aliquota_icms))
        _sub(g, 'vICMS', f2(item.valor_icms))
    elif cst == '20':
        _sub(g, 'modBC', '3')
        _sub(g, 'pRedBC', f4(item.reducao_bc_icms))
        _sub(g, 'vBC', f2(item.base_icms))
        _sub(g, 'pICMS', f4(item.aliquota_icms))
        _sub(g, 'vICMS', f2(item.valor_icms))
    elif cst == '90' and com_valor:
        _sub(g, 'modBC', '3')
        _sub(g, 'vBC', f2(item.base_icms))
        if item.reducao_bc_icms > 0:
            _sub(g, 'pRedBC', f4(item.reducao_bc_icms))
        _sub(g, 'pICMS', f4(item.aliquota_icms))
        _sub(g, 'vICMS', f2(item.valor_icms))


def _pis_cofins(imposto, tag, cst, aliquota, valor, base):
    pai = _sub(imposto, tag)
    if cst in CST_PIS_NT:
        g = _sub(pai, f'{tag}NT')
        _sub(g, 'CST', cst)
        return
    g = _sub(pai, f'{tag}Aliq' if cst in CST_PIS_ALIQ else f'{tag}Outr')
    _sub(g, 'CST', cst)
    _sub(g, 'vBC', f2(base))
    _sub(g, f'p{tag}', f4(aliquota))
    _sub(g, f'v{tag}', f2(valor))


def montar_xml(nota, itens, config, momento):
    """O <NFe> sem assinatura. ``nota.chave`` já precisa estar gerada."""
    emit_cfg = nota.emitente
    ambiente = config.ambiente
    id_dest = destino(nota)
    raiz = etree.Element(f'{{{NS}}}NFe', nsmap={None: NS})
    inf = _sub(raiz, 'infNFe')
    inf.set('versao', VERSAO)
    inf.set('Id', 'NFe' + nota.chave)

    ide = _sub(inf, 'ide')
    _sub(ide, 'cUF', CODIGOS_UF[emit_cfg.uf])
    _sub(ide, 'cNF', nota.codigo_numerico)
    _sub(ide, 'natOp', texto(nota.natureza, 60))
    _sub(ide, 'mod', '55')
    _sub(ide, 'serie', str(nota.serie))
    _sub(ide, 'nNF', str(nota.numero))
    _sub(ide, 'dhEmi', data_hora(momento))
    _sub(ide, 'dhSaiEnt', data_hora(momento))
    _sub(ide, 'tpNF', nota.tipo_operacao)
    _sub(ide, 'idDest', id_dest)
    _sub(ide, 'cMunFG', so_digitos(emit_cfg.codigo_municipio))
    _sub(ide, 'tpImp', '1')
    _sub(ide, 'tpEmis', '1')
    _sub(ide, 'cDV', nota.chave[-1])
    _sub(ide, 'tpAmb', ambiente)
    _sub(ide, 'finNFe', nota.finalidade)
    _sub(ide, 'indFinal', '1' if nota.consumidor_final else '0')
    _sub(ide, 'indPres', nota.presenca)
    if nota.presenca in ('2', '3', '4', '9'):
        _sub(ide, 'indIntermed', '0')
    _sub(ide, 'procEmi', '0')
    _sub(ide, 'verProc', VER_PROC)
    if nota.chave_referenciada:
        ref = _sub(ide, 'NFref')
        _sub(ref, 'refNFe', so_digitos(nota.chave_referenciada))

    emit = _sub(inf, 'emit')
    _sub(emit, 'CNPJ', emit_cfg.cnpj)
    _sub(emit, 'xNome', texto(emit_cfg.razao_social, 60))
    if texto(emit_cfg.nome_fantasia):
        _sub(emit, 'xFant', texto(emit_cfg.nome_fantasia, 60))
    _endereco(emit, 'enderEmit', emit_cfg.logradouro, emit_cfg.numero, emit_cfg.complemento, emit_cfg.bairro,
              emit_cfg.codigo_municipio, emit_cfg.municipio, emit_cfg.uf, emit_cfg.cep, emit_cfg.telefone)
    _sub(emit, 'IE', so_digitos(emit_cfg.ie))
    _sub(emit, 'CRT', emit_cfg.crt)

    dest = _sub(inf, 'dest')
    documento = so_digitos(nota.dest_documento)
    _sub(dest, 'CNPJ' if len(documento) == 14 else 'CPF', documento)
    _sub(dest, 'xNome', XNOME_HOMOLOGACAO if ambiente == '2' else texto(nota.dest_nome, 60))
    _endereco(dest, 'enderDest', nota.dest_logradouro, nota.dest_numero, nota.dest_complemento, nota.dest_bairro,
              nota.dest_codigo_municipio, nota.dest_municipio, nota.dest_uf, nota.dest_cep, nota.dest_telefone)
    _sub(dest, 'indIEDest', nota.dest_ind_ie)
    if nota.dest_ind_ie == '1' or (nota.dest_ind_ie == '9' and so_digitos(nota.dest_ie)):
        _sub(dest, 'IE', so_digitos(nota.dest_ie))
    if texto(nota.dest_email):
        _sub(dest, 'email', texto(nota.dest_email, 60))

    fretes, seguros, outras = rateios(nota, itens)
    for n, (item, frete, seguro, outra) in enumerate(zip(itens, fretes, seguros, outras), start=1):
        det = _sub(inf, 'det')
        det.set('nItem', str(n))
        prod = _sub(det, 'prod')
        _sub(prod, 'cProd', texto(item.codigo, 60))
        ean = so_digitos(item.ean)
        _sub(prod, 'cEAN', ean if len(ean) in (8, 12, 13, 14) else 'SEM GTIN')
        _sub(prod, 'xProd', texto(item.descricao, 120))
        _sub(prod, 'NCM', item.ncm)
        if item.cest:
            _sub(prod, 'CEST', item.cest)
        _sub(prod, 'CFOP', item.cfop)
        _sub(prod, 'uCom', texto(item.unidade, 6) or 'UN')
        _sub(prod, 'qCom', f4(item.quantidade))
        _sub(prod, 'vUnCom', f_unit(item.valor_unitario))
        _sub(prod, 'vProd', f2(item.valor_total))
        _sub(prod, 'cEANTrib', ean if len(ean) in (8, 12, 13, 14) else 'SEM GTIN')
        _sub(prod, 'uTrib', texto(item.unidade, 6) or 'UN')
        _sub(prod, 'qTrib', f4(item.quantidade))
        _sub(prod, 'vUnTrib', f_unit(item.valor_unitario))
        if frete:
            _sub(prod, 'vFrete', f2(frete))
        if seguro:
            _sub(prod, 'vSeg', f2(seguro))
        if item.valor_desconto:
            _sub(prod, 'vDesc', f2(item.valor_desconto))
        if outra:
            _sub(prod, 'vOutro', f2(outra))
        _sub(prod, 'indTot', '1')
        imposto = _sub(det, 'imposto')
        _icms(imposto, item, emit_cfg.simples)
        base_pc = item.valor_total - item.valor_desconto
        _pis_cofins(imposto, 'PIS', item.cst_pis, item.aliquota_pis, item.valor_pis, base_pc)
        _pis_cofins(imposto, 'COFINS', item.cst_cofins, item.aliquota_cofins, item.valor_cofins, base_pc)
        if texto(item.info_adicional):
            _sub(det, 'infAdProd', texto(item.info_adicional, 500))

    total = _sub(_sub(inf, 'total'), 'ICMSTot')
    for tag, valor in (('vBC', nota.base_icms), ('vICMS', nota.valor_icms), ('vICMSDeson', 0), ('vFCP', 0),
                       ('vBCST', 0), ('vST', 0), ('vFCPST', 0), ('vFCPSTRet', 0), ('vProd', nota.valor_produtos),
                       ('vFrete', nota.valor_frete), ('vSeg', nota.valor_seguro), ('vDesc', nota.valor_desconto),
                       ('vII', 0), ('vIPI', 0), ('vIPIDevol', 0), ('vPIS', nota.valor_pis),
                       ('vCOFINS', nota.valor_cofins), ('vOutro', nota.valor_outras), ('vNF', nota.valor_total)):
        _sub(total, tag, f2(valor))

    transp = _sub(inf, 'transp')
    _sub(transp, 'modFrete', nota.modalidade_frete)

    pag = _sub(inf, 'pag')
    det_pag = _sub(pag, 'detPag')
    forma = '90' if nota.finalidade in ('3', '4') else nota.forma_pagamento
    _sub(det_pag, 'tPag', forma)
    if forma == '99':
        _sub(det_pag, 'xPag', 'Outros')
    _sub(det_pag, 'vPag', f2(0 if forma == '90' else nota.valor_total))
    if forma in ('03', '04', '17'):
        card = _sub(det_pag, 'card')
        _sub(card, 'tpIntegra', '2')

    complementar = info_complementar(nota)
    if complementar:
        _sub(_sub(inf, 'infAdic'), 'infCpl', complementar)

    if so_digitos(config.resp_tec_cnpj) and texto(config.resp_tec_contato) and texto(config.resp_tec_email):
        resp = _sub(inf, 'infRespTec')
        _sub(resp, 'CNPJ', so_digitos(config.resp_tec_cnpj))
        _sub(resp, 'xContato', texto(config.resp_tec_contato, 60))
        _sub(resp, 'email', texto(config.resp_tec_email, 60))
        _sub(resp, 'fone', so_digitos(config.resp_tec_fone) or '0000000000')
    return raiz


def info_complementar(nota):
    partes = [texto(nota.info_complementar)]
    if nota.finalidade == '4' and nota.chave_referenciada:
        chave = so_digitos(nota.chave_referenciada)
        auto = f'Devolucao referente a NF-e numero {int(chave[25:34])} serie {int(chave[22:25])}, chave {chave}.'
        if auto not in partes[0]:
            partes.append(auto)
    return texto('. '.join(p.rstrip('. ') for p in partes if p) + '.', 5000) if any(partes) else ''


# ── Assinatura ──────────────────────────────────────────────────────────────
def _c14n(elemento):
    return etree.tostring(elemento, method='c14n', exclusive=False, with_comments=False)


def assinar(raiz, tag_inf, chave_privada, certificado):
    """XMLDSig envelopada do elemento ``tag_inf`` (RSA-SHA1, C14N 1.0), como o leiaute pede."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    raiz = etree.fromstring(etree.tostring(raiz))   # documento próprio: é assim que a SEFAZ lê
    inf = raiz.find(f'{{{NS}}}{tag_inf}')
    digest = base64.b64encode(hashlib.sha1(_c14n(inf)).digest()).decode()
    ds = lambda tag: f'{{{NS_DS}}}{tag}'  # noqa: E731
    assinatura = etree.SubElement(raiz, ds('Signature'), nsmap={None: NS_DS})
    info = etree.SubElement(assinatura, ds('SignedInfo'))
    etree.SubElement(info, ds('CanonicalizationMethod'), Algorithm=C14N)
    etree.SubElement(info, ds('SignatureMethod'), Algorithm='http://www.w3.org/2000/09/xmldsig#rsa-sha1')
    ref = etree.SubElement(info, ds('Reference'), URI='#' + inf.get('Id'))
    transforms = etree.SubElement(ref, ds('Transforms'))
    etree.SubElement(transforms, ds('Transform'), Algorithm='http://www.w3.org/2000/09/xmldsig#enveloped-signature')
    etree.SubElement(transforms, ds('Transform'), Algorithm=C14N)
    etree.SubElement(ref, ds('DigestMethod'), Algorithm='http://www.w3.org/2000/09/xmldsig#sha1')
    etree.SubElement(ref, ds('DigestValue')).text = digest
    valor = chave_privada.sign(_c14n(info), padding.PKCS1v15(), hashes.SHA1())
    etree.SubElement(assinatura, ds('SignatureValue')).text = base64.b64encode(valor).decode()
    x509 = etree.SubElement(etree.SubElement(assinatura, ds('KeyInfo')), ds('X509Data'))
    etree.SubElement(x509, ds('X509Certificate')).text = base64.b64encode(
        certificado.public_bytes(serialization.Encoding.DER)).decode()
    return raiz


def verificar_assinatura(raiz, tag_inf):
    """Confere digest e assinatura (teste e conferência)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    raiz = etree.fromstring(etree.tostring(raiz))
    etree.cleanup_namespaces(raiz)
    assinatura = raiz.find(f'{{{NS_DS}}}Signature')
    inf = raiz.find(f'{{{NS}}}{tag_inf}')
    if assinatura is None or inf is None:
        return False
    info = assinatura.find(f'{{{NS_DS}}}SignedInfo')
    digest = info.find(f'.//{{{NS_DS}}}DigestValue').text
    raiz.remove(assinatura)
    if base64.b64encode(hashlib.sha1(_c14n(inf)).digest()).decode() != digest:
        return False
    cert = x509.load_der_x509_certificate(base64.b64decode(assinatura.find(f'.//{{{NS_DS}}}X509Certificate').text))
    try:
        cert.public_key().verify(base64.b64decode(assinatura.find(f'{{{NS_DS}}}SignatureValue').text),
                                 _c14n(info), padding.PKCS1v15(), hashes.SHA1())
    except Exception:  # noqa: BLE001
        return False
    return True


# ── Conversa com a SEFAZ ────────────────────────────────────────────────────
def url_do_servico(servico, uf, ambiente):
    _wsdl, caminho_svrs, caminho_sp = SERVICOS[servico]
    if uf == 'SP':
        return (SP if ambiente == '1' else SP_HOM) + caminho_sp
    if uf in UFS_SVRS:
        return (SVRS if ambiente == '1' else SVRS_HOM) + caminho_svrs
    raise NfeInvalida(f'A emissão pelo portal ainda não tem o endereço da SEFAZ de {uf} (hoje: ES, RJ, SP e SVRS).')


def envelope(servico, corpo):
    wsdl = f'http://www.portalfiscal.inf.br/nfe/wsdl/{SERVICOS[servico][0]}'
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
            f'<soap12:Body><nfeDadosMsg xmlns="{wsdl}">{corpo}</nfeDadosMsg></soap12:Body></soap12:Envelope>')


def _transporte_https(servico, uf, ambiente, corpo, config):
    """O envio de verdade (TLS com o certificado A1). Só roda com a emissão ligada."""
    import requests
    if not config.ligado:
        raise NfeInvalida('A emissão de NF-e está desligada.')
    url = url_do_servico(servico, uf, ambiente)
    acao = f'http://www.portalfiscal.inf.br/nfe/wsdl/{SERVICOS[servico][0]}/{ACOES_SOAP[servico]}'
    with _arquivos_pem(config) as (cert, chave):
        try:
            resposta = requests.post(url, data=corpo.encode('utf-8'), cert=(cert, chave), timeout=TIMEOUT,
                                     verify=getattr(settings, 'NFE_CA_BUNDLE', '') or True,
                                     headers={'Content-Type': f'application/soap+xml; charset=utf-8; action="{acao}"'})
        except requests.RequestException as exc:
            raise SefazErro(f'A SEFAZ não respondeu: {exc}') from exc
    if resposta.status_code != 200:
        raise SefazErro(f'A SEFAZ respondeu HTTP {resposta.status_code}: {resposta.text[:300]}')
    return resposta.content


def _enviar(servico, uf, config, corpo, transporte=None):
    transporte = transporte or _transporte_https
    bruto = transporte(servico, uf, config.ambiente, envelope(servico, corpo), config)
    try:
        return etree.fromstring(bruto if isinstance(bruto, bytes) else bruto.encode('utf-8'))
    except etree.XMLSyntaxError as exc:
        raise SefazErro(f'Resposta da SEFAZ ilegível: {exc}') from exc


def _t(el, tag):
    if el is None:
        return ''
    achado = el.find(f'{{{NS}}}{tag}')
    return (achado.text or '').strip() if achado is not None and achado.text else ''


def _achar(raiz, tag):
    achado = raiz.find(f'.//{{{NS}}}{tag}')
    if achado is None:
        falha = raiz.find('.//{http://www.w3.org/2003/05/soap-envelope}Text')
        raise SefazErro(f'Resposta sem {tag}: {falha.text if falha is not None else etree.tostring(raiz)[:200]!r}')
    return achado


def status_servico(config, uf, transporte=None):
    corpo = (f'<consStatServ xmlns="{NS}" versao="{VERSAO}"><tpAmb>{config.ambiente}</tpAmb>'
             f'<cUF>{CODIGOS_UF[uf]}</cUF><xServ>STATUS</xServ></consStatServ>')
    ret = _achar(_enviar('status', uf, config, corpo, transporte), 'retConsStatServ')
    return {'cstat': _t(ret, 'cStat'), 'motivo': _t(ret, 'xMotivo'), 'tempo_medio': _t(ret, 'tMed')}


def _nfe_proc(xml_assinado, prot):
    proc = etree.Element(f'{{{NS}}}nfeProc', nsmap={None: NS}, versao=VERSAO)
    proc.append(etree.fromstring(xml_assinado.encode('utf-8')))
    proc.append(etree.fromstring(etree.tostring(prot)))
    return '<?xml version="1.0" encoding="UTF-8"?>' + etree.tostring(proc, encoding='unicode')


def _aplicar_protocolo(nota, prot, operacao):
    """Lê o protNFe e muda a situação da nota."""
    inf = prot.find(f'{{{NS}}}infProt')
    cstat, motivo = _t(inf, 'cStat'), _t(inf, 'xMotivo')
    nota.cstat, nota.motivo = cstat[:10], motivo[:500]
    nota.anotar(operacao, cstat, motivo)
    if cstat in AUTORIZADA or cstat in DENEGADA:
        if _t(inf, 'chNFe') and _t(inf, 'chNFe') != nota.chave:
            raise SefazErro('O protocolo da SEFAZ é de outra chave.')
        nota.protocolo = _t(inf, 'nProt')[:20]
        nota.xml_autorizado = _nfe_proc(nota.xml_assinado, prot)
        nota.status = 'AUTORIZADA' if cstat in AUTORIZADA else 'DENEGADA'
        nota.autorizada_em = timezone.now()
        nota.recibo = ''
    else:
        nota.status = 'REJEITADA'
        nota.recibo = ''


def _proximo_numero(emitente_id, ambiente):
    """Produção e homologação numeram separado: teste não abre buraco na numeração de verdade."""
    from .models_nfe import EmitenteNFe
    emitente = EmitenteNFe.objects.select_for_update().get(pk=emitente_id)
    campo = 'proximo_numero' if ambiente == '1' else 'proximo_numero_homologacao'
    numero = getattr(emitente, campo)
    setattr(emitente, campo, numero + 1)
    emitente.save(update_fields=[campo, 'atualizado_em'])
    return emitente.serie, numero


def enviar(nota, usuario=None, transporte=None, agora=None):
    """Numera (na primeira vez), assina e envia. Devolve a nota já gravada."""
    from .models_nfe import ConfiguracaoNFe, NotaFiscalEmitida

    config = ConfiguracaoNFe.atual()
    if not config.ligado:
        raise NfeInvalida('A emissão de NF-e está desligada. O SUPERADMIN liga em Notas fiscais → Configuração.')
    if not nota.editavel:
        raise NfeInvalida(f'A nota está "{nota.get_status_display()}": não dá para enviar de novo.')
    itens = list(nota.itens.all())
    calcular(nota, itens)
    erros = validar(nota, itens)
    if erros:
        raise NfeInvalida(' · '.join(erros))
    chave_privada, cert, _cadeia = carregar_certificado(config)
    do_cert = cnpj_do_certificado(cert)
    if do_cert and do_cert[:8] != nota.emitente.cnpj[:8]:
        raise NfeInvalida('O certificado é de outra empresa (a raiz do CNPJ não bate com a do emitente).')
    momento = agora or timezone.now()
    with transaction.atomic():
        nota = NotaFiscalEmitida.objects.select_for_update().get(pk=nota.pk)
        if not nota.editavel:
            raise NfeInvalida('Outra pessoa já enviou esta nota.')
        itens = list(nota.itens.all())
        calcular(nota, itens)
        if nota.numero is None or nota.ambiente != config.ambiente:
            nota.serie, nota.numero = _proximo_numero(nota.emitente_id, config.ambiente)
            nota.ambiente = config.ambiente
            nota.codigo_numerico = ''
        nota.chave = gerar_chave(nota, momento)
        nota.emitida_em = momento
        assinado = assinar(montar_xml(nota, itens, config, momento), 'infNFe', chave_privada, cert)
        nota.xml_assinado = etree.tostring(assinado, encoding='unicode')
        nota.status = 'PROCESSANDO'
        for item in itens:
            item.save()
        nota.save()
    id_lote = momento.strftime('%y%m%d%H%M%S') + f'{nota.pk % 1000:03d}'
    corpo = (f'<enviNFe xmlns="{NS}" versao="{VERSAO}"><idLote>{id_lote}</idLote><indSinc>1</indSinc>'
             + nota.xml_assinado + '</enviNFe>')
    try:
        raiz = _enviar('autorizacao', nota.emitente.uf, config, corpo, transporte)
        ret = _achar(raiz, 'retEnviNFe')
        cstat, motivo = _t(ret, 'cStat'), _t(ret, 'xMotivo')
        prot = ret.find(f'{{{NS}}}protNFe')
        if cstat == '104' and prot is not None:
            _aplicar_protocolo(nota, prot, 'Envio')
        elif cstat == '103':
            nota.recibo = _t(ret.find(f'{{{NS}}}infRec'), 'nRec')[:20]
            nota.cstat, nota.motivo = cstat, motivo[:500]
            nota.anotar('Envio', cstat, motivo + ' — consultar o recibo')
        else:
            nota.status, nota.cstat, nota.motivo = 'REJEITADA', cstat[:10], motivo[:500]
            nota.anotar('Envio', cstat, motivo)
    except SefazErro as exc:
        # Não se sabe se a SEFAZ recebeu: fica em processamento e a consulta pela chave resolve.
        nota.cstat, nota.motivo = 'ERRO', str(exc)[:500]
        nota.anotar('Envio', 'ERRO', str(exc))
    nota.save()
    if nota.status == 'REJEITADA' and nota.cstat == '204':
        # Duplicidade: a nota pode já estar autorizada — a consulta pela chave confirma.
        with contextlib.suppress(SefazErro, NfeInvalida):
            consultar(nota, transporte=transporte)
    return nota


def consultar(nota, transporte=None):
    """Situação na SEFAZ: pelo recibo (lote em processamento) ou pela chave."""
    from .models_nfe import ConfiguracaoNFe
    config = ConfiguracaoNFe.atual()
    if not config.ligado:
        raise NfeInvalida('A emissão de NF-e está desligada.')
    if not nota.chave:
        raise NfeInvalida('A nota ainda não foi enviada.')
    if config.ambiente != nota.ambiente:
        config.ambiente = nota.ambiente   # pergunta no ambiente em que a nota foi emitida (não grava)
    uf = nota.emitente.uf
    if nota.recibo:
        corpo = (f'<consReciNFe xmlns="{NS}" versao="{VERSAO}"><tpAmb>{nota.ambiente}</tpAmb>'
                 f'<nRec>{nota.recibo}</nRec></consReciNFe>')
        ret = _achar(_enviar('retorno', uf, config, corpo, transporte), 'retConsReciNFe')
        cstat, motivo = _t(ret, 'cStat'), _t(ret, 'xMotivo')
        prot = ret.find(f'{{{NS}}}protNFe')
        if cstat == '104' and prot is not None:
            _aplicar_protocolo(nota, prot, 'Consulta do recibo')
        elif cstat == '105':
            nota.anotar('Consulta do recibo', cstat, motivo)
        else:
            nota.recibo = ''
            nota.anotar('Consulta do recibo', cstat, motivo)
        nota.save()
        return nota
    corpo = (f'<consSitNFe xmlns="{NS}" versao="{VERSAO}"><tpAmb>{nota.ambiente}</tpAmb>'
             f'<xServ>CONSULTAR</xServ><chNFe>{nota.chave}</chNFe></consSitNFe>')
    ret = _achar(_enviar('consulta', uf, config, corpo, transporte), 'retConsSitNFe')
    cstat, motivo = _t(ret, 'cStat'), _t(ret, 'xMotivo')
    prot = ret.find(f'{{{NS}}}protNFe')
    if prot is not None and nota.status in ('PROCESSANDO', 'REJEITADA'):
        _aplicar_protocolo(nota, prot, 'Consulta pela chave')
    else:
        nota.anotar('Consulta pela chave', cstat, motivo)
        if cstat == '217' and nota.status == 'PROCESSANDO':
            nota.status = 'REJEITADA'     # a SEFAZ não tem a nota: pode corrigir e reenviar com o mesmo número
            nota.cstat, nota.motivo = cstat, motivo[:500]
    if cstat == '101' and nota.status == 'AUTORIZADA':
        nota.status = 'CANCELADA'
        nota.cancelada_em = nota.cancelada_em or timezone.now()
    nota.save()
    return nota


# ── Eventos: cancelamento e carta de correção ──────────────────────────────
def _montar_evento(nota, tipo, seq, detalhes, ambiente, agora):
    evento = etree.Element(f'{{{NS}}}evento', nsmap={None: NS}, versao='1.00')
    inf = etree.SubElement(evento, f'{{{NS}}}infEvento', Id=f'ID{tipo}{nota.chave}{seq:02d}')
    for tag, valor in (('cOrgao', CODIGOS_UF[nota.emitente.uf]), ('tpAmb', ambiente), ('CNPJ', nota.emitente.cnpj),
                       ('chNFe', nota.chave), ('dhEvento', data_hora(agora)), ('tpEvento', tipo),
                       ('nSeqEvento', str(seq)), ('verEvento', '1.00')):
        etree.SubElement(inf, f'{{{NS}}}{tag}').text = valor
    det = etree.SubElement(inf, f'{{{NS}}}detEvento', versao='1.00')
    for tag, valor in detalhes:
        etree.SubElement(det, f'{{{NS}}}{tag}').text = valor
    return evento


def _registrar_evento(nota, tipo, texto_evento, detalhes, usuario, transporte, agora):
    from .models_nfe import ConfiguracaoNFe, EventoNotaFiscal
    config = ConfiguracaoNFe.atual()
    if not config.ligado:
        raise NfeInvalida('A emissão de NF-e está desligada.')
    config.ambiente = nota.ambiente
    chave_privada, cert, _cadeia = carregar_certificado(config)
    agora = agora or timezone.now()
    anteriores = nota.eventos.filter(tipo=tipo, sucesso=True).count()
    seq = anteriores + 1
    if tipo == '110110' and seq > 20:
        raise NfeInvalida('A nota já tem 20 cartas de correção (o limite da SEFAZ).')
    evento = assinar(_montar_evento(nota, tipo, seq, detalhes, nota.ambiente, agora), 'infEvento', chave_privada, cert)
    xml_evento = etree.tostring(evento, encoding='unicode')
    registro = EventoNotaFiscal(nota=nota, tipo=tipo, sequencia=seq, texto=texto_evento, enviado_por=usuario,
                                enviado_em=agora, xml_enviado=xml_evento)
    id_lote = agora.strftime('%y%m%d%H%M%S') + f'{nota.pk % 1000:03d}'
    corpo = f'<envEvento xmlns="{NS}" versao="1.00"><idLote>{id_lote}</idLote>{xml_evento}</envEvento>'
    try:
        raiz = _enviar('evento', nota.emitente.uf, config, corpo, transporte)
        ret = _achar(raiz, 'retEnvEvento')
        ret_evento = ret.find(f'{{{NS}}}retEvento')
        inf = ret_evento.find(f'{{{NS}}}infEvento') if ret_evento is not None else None
        if inf is None:
            registro.cstat, registro.motivo = _t(ret, 'cStat')[:10], _t(ret, 'xMotivo')[:500]
        else:
            registro.cstat, registro.motivo = _t(inf, 'cStat')[:10], _t(inf, 'xMotivo')[:500]
            registro.protocolo = _t(inf, 'nProt')[:20]
            registro.sucesso = registro.cstat in EVENTO_REGISTRADO
            if registro.sucesso:
                proc = etree.Element(f'{{{NS}}}procEventoNFe', nsmap={None: NS}, versao='1.00')
                proc.append(etree.fromstring(xml_evento.encode('utf-8')))
                proc.append(etree.fromstring(etree.tostring(ret_evento)))
                registro.xml_retorno = ('<?xml version="1.0" encoding="UTF-8"?>'
                                        + etree.tostring(proc, encoding='unicode'))
        if not registro.xml_retorno:
            registro.xml_retorno = etree.tostring(raiz, encoding='unicode')[:20000]
    except SefazErro as exc:
        registro.cstat, registro.motivo = 'ERRO', str(exc)[:500]
    registro.save()
    nota.anotar(dict(EventoNotaFiscal.TIPOS)[tipo], registro.cstat, registro.motivo)
    if registro.sucesso and tipo == '110111':
        nota.status = 'CANCELADA'
        nota.cancelada_em = agora
    nota.save()
    return registro


def cancelar(nota, justificativa, usuario=None, transporte=None, agora=None):
    justificativa = sem_acento(texto(justificativa))
    if nota.status != 'AUTORIZADA':
        raise NfeInvalida('Só nota autorizada pode ser cancelada.')
    if not 15 <= len(justificativa) <= 255:
        raise NfeInvalida('A justificativa do cancelamento tem de 15 a 255 caracteres.')
    return _registrar_evento(nota, '110111', justificativa,
                             [('descEvento', 'Cancelamento'), ('nProt', nota.protocolo), ('xJust', justificativa)],
                             usuario, transporte, agora)


def corrigir(nota, correcao, usuario=None, transporte=None, agora=None):
    correcao = sem_acento(texto(correcao))
    if nota.status != 'AUTORIZADA':
        raise NfeInvalida('Carta de correção só vale para nota autorizada.')
    if not 15 <= len(correcao) <= 1000:
        raise NfeInvalida('A correção tem de 15 a 1000 caracteres.')
    return _registrar_evento(nota, '110110', correcao,
                             [('descEvento', 'Carta de Correcao'), ('xCorrecao', correcao),
                              ('xCondUso', CONDICAO_CCE)], usuario, transporte, agora)


# ── Devolução a partir da nota original ─────────────────────────────────────
ST_FINAIS = ('401', '402', '403', '405')


def cfop_de_devolucao(cfop_original, saida, mesmo_estado):
    """5102 recebida → 5202/6202 (devolução de compra); nossa 5102 → 1202/2202 (devolução de venda)."""
    final = (cfop_original or '')[1:]
    if saida:
        prefixo = '5' if mesmo_estado else '6'
    else:
        prefixo = '1' if mesmo_estado else '2'
    return prefixo + ('411' if final in ST_FINAIS else '202')


def _xml_original(chave):
    from contagem_caixa.models import NotaRecebida

    from .models_nfe import NotaFiscalEmitida
    emitida = NotaFiscalEmitida.objects.filter(chave=chave).exclude(xml_autorizado='').first()
    if emitida:
        return emitida.xml_autorizado
    recebida = NotaRecebida.objects.filter(chave=chave).exclude(xml_completo='').first()
    return recebida.xml_completo if recebida else ''


def dados_para_devolucao(chave, emitentes):
    """Lê a NF-e original (recebida ou emitida por nós) e devolve o rascunho da devolução.

    ``emitentes``: os EmitenteNFe que a pessoa pode usar. Devolve um dict com
    'nota' (campos) e 'itens' (lista de dicts) — ou levanta NfeInvalida.
    """
    chave = so_digitos(chave)
    if not chave_valida(chave):
        raise NfeInvalida('A chave tem 44 números (confira o dígito verificador).')
    bruto = _xml_original(chave)
    if not bruto:
        raise NfeInvalida('O portal não tem o XML completo desta nota. Busque a nota em PIS/Cofins (manifestar '
                          'ciência baixa o XML) ou preencha os itens à mão.')
    try:
        raiz = etree.fromstring(bruto.encode('utf-8') if isinstance(bruto, str) else bruto)
    except etree.XMLSyntaxError as exc:
        raise NfeInvalida('O XML guardado da nota original não abre.') from exc
    inf = raiz.find(f'.//{{{NS}}}infNFe')
    if inf is None:
        raise NfeInvalida('O XML guardado não é de NF-e.')
    emit, dest, ide = inf.find(f'{{{NS}}}emit'), inf.find(f'{{{NS}}}dest'), inf.find(f'{{{NS}}}ide')
    cnpj_emit = _t(emit, 'CNPJ') or _t(emit, 'CPF')
    cnpj_dest = (_t(dest, 'CNPJ') or _t(dest, 'CPF')) if dest is not None else ''
    por_cnpj = {e.cnpj: e for e in emitentes}
    if cnpj_emit in por_cnpj:          # a nota é nossa: o cliente devolve → entrada
        nosso, outro, outro_end, saida = por_cnpj[cnpj_emit], dest, 'enderDest', False
    elif cnpj_dest in por_cnpj:        # nota do fornecedor para nós: devolvemos → saída
        nosso, outro, outro_end, saida = por_cnpj[cnpj_dest], emit, 'enderEmit', True
    else:
        raise NfeInvalida('Nenhum emitente cadastrado (que você possa usar) é emitente nem destinatário desta nota.')
    if outro is None:
        raise NfeInvalida('A nota original não tem destinatário identificado.')
    end = outro.find(f'{{{NS}}}{outro_end}')
    uf_outro = _t(end, 'UF')
    mesmo_estado = uf_outro == nosso.uf
    ie_outro = _t(outro, 'IE')
    ind_ie = _t(outro, 'indIEDest') or ('1' if ie_outro and ie_outro.upper() != 'ISENTO' else '9')
    if outro_end == 'enderEmit':
        ind_ie = '1' if ie_outro and ie_outro.upper() != 'ISENTO' else '2'
    numero_original, serie_original = _t(ide, 'nNF'), _t(ide, 'serie')
    emissao_original = _t(ide, 'dhEmi')[:10]
    nota = {
        'emitente': nosso.pk, 'tipo_operacao': '1' if saida else '0', 'finalidade': '4',
        'natureza': 'Devolucao de compra' if saida else 'Devolucao de venda',
        'chave_referenciada': chave,
        'dest_documento': _t(outro, 'CNPJ') or _t(outro, 'CPF'), 'dest_nome': _t(outro, 'xNome')[:60],
        'dest_ie': '' if ie_outro.upper() == 'ISENTO' else ie_outro, 'dest_ind_ie': ind_ie,
        'dest_email': _t(outro, 'email')[:60], 'dest_telefone': _t(end, 'fone'),
        'dest_logradouro': _t(end, 'xLgr'), 'dest_numero': _t(end, 'nro') or 'S/N',
        'dest_complemento': _t(end, 'xCpl'), 'dest_bairro': _t(end, 'xBairro'),
        'dest_codigo_municipio': _t(end, 'cMun'), 'dest_municipio': _t(end, 'xMun'), 'dest_uf': uf_outro,
        'dest_cep': _t(end, 'CEP'), 'presenca': '0', 'forma_pagamento': '90', 'modalidade_frete': '9',
        'info_complementar': '',
        'original': {'numero': numero_original, 'serie': serie_original, 'emissao': emissao_original,
                     'emitente': _t(emit, 'xNome'), 'valor': _t(inf.find(f'.//{{{NS}}}ICMSTot'), 'vNF')},
    }
    itens = []
    for det in inf.findall(f'{{{NS}}}det'):
        prod = det.find(f'{{{NS}}}prod')
        imposto = det.find(f'{{{NS}}}imposto')
        icms = imposto.find(f'{{{NS}}}ICMS') if imposto is not None else None
        grupo = icms[0] if icms is not None and len(icms) else None
        cst_original = _t(grupo, 'CST') or _t(grupo, 'CSOSN')
        aliquota = _t(grupo, 'pICMS') or '0'
        if nosso.simples:
            cst = '900' if Decimal(aliquota or '0') > 0 else '400'
        else:
            cst = cst_original if cst_original in CST_NORMAL else ('90' if Decimal(aliquota or '0') > 0 else '41')
        itens.append({
            'codigo': _t(prod, 'cProd'), 'descricao': _t(prod, 'xProd')[:120],
            'ean': _t(prod, 'cEAN') if _t(prod, 'cEAN') != 'SEM GTIN' else '',
            'ncm': _t(prod, 'NCM'), 'cest': _t(prod, 'CEST'),
            'cfop': cfop_de_devolucao(_t(prod, 'CFOP'), saida, mesmo_estado),
            'unidade': _t(prod, 'uCom')[:6] or 'UN', 'quantidade': _t(prod, 'qCom') or '1',
            'valor_unitario': _t(prod, 'vUnCom') or '0', 'valor_desconto': _t(prod, 'vDesc') or '0',
            'origem': _t(grupo, 'orig') or '0', 'cst_icms': cst,
            'reducao_bc_icms': _t(grupo, 'pRedBC') or '0', 'aliquota_icms': aliquota,
            'cst_pis': '49' if saida else '98', 'aliquota_pis': '0',
            'cst_cofins': '49' if saida else '98', 'aliquota_cofins': '0', 'info_adicional': '',
        })
    if not itens:
        raise NfeInvalida('A nota original não tem itens legíveis.')
    return {'nota': nota, 'itens': itens}
