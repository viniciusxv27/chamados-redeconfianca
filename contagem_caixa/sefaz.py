"""Notas emitidas contra o CNPJ da empresa — NF-e Distribuição DFe (SEFAZ).

A Receita não publica uma API aberta das notas de um CNPJ. O canal oficial é o
serviço **NFeDistribuicaoDFe** do Ambiente Nacional: com o certificado digital
A1 da empresa (e-CNPJ), ele devolve, em ordem de NSU, todo documento fiscal em
que o CNPJ aparece — as NF-e que fornecedores emitiram para nós, os eventos
(cancelamento) e, depois da manifestação, o XML completo.

Aqui usamos os **resumos** (resNFe): emitente, CNPJ, IE, emissão, valor, chave
e situação. Número, série e modelo saem da chave de acesso. É o bastante para
a visão "o que foi emitido contra a gente × o que já foi lançado no PIS/Cofins";
o XML completo chega sozinho quando a nota for manifestada (procNFe).

Regras da SEFAZ que este módulo respeita:

- consulta por ``ultNSU``, em lotes de até 50 documentos, até ``ultNSU == maxNSU``;
- cStat 137 (nada novo) ou 656 (consumo indevido) → só consultar de novo depois
  de 1 hora; insistir antes disso bloqueia o CNPJ;
- o certificado da matriz consulta as filiais (mesma raiz de 8 dígitos).

Configuração (no ``.env``; nasce desligado):

- ``SEFAZ_CERTIFICADO``: caminho do arquivo .pfx/.p12 — ou
  ``SEFAZ_CERTIFICADO_B64``: o .pfx em base64 (para container);
- ``SEFAZ_CERTIFICADO_SENHA``;
- ``SEFAZ_CNPJS``: CNPJs a acompanhar, separados por vírgula — de preferência
  com a UF: ``09.163.602/0001-34:ES,09.163.602/0012-97:RJ``;
- ``SEFAZ_AMBIENTE``: 1 produção, 2 homologação.

A UF do pedido (``cUFAutor``) é a de cada CNPJ consultado, não a da sede: o
CNPJ do RJ vai com 33, o de SP com 35. Ela sai do cadastro da Receita
(``cnpj.buscar``); ``SEFAZ_UF`` só vale quando a Receita não responde. As
notas vêm do Ambiente Nacional, de qualquer estado, seja qual for a UF.

O certificado consulta os CNPJs da mesma raiz (8 primeiros dígitos) que o dele:
CNPJ de outra raiz é outra empresa e precisa do próprio certificado — o portal
avisa e não consulta (a SEFAZ recusaria).
"""
from __future__ import annotations

import base64
import contextlib
import gzip
import logging
import os
import tempfile
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from lxml import etree

from . import cnpj as receita

logger = logging.getLogger(__name__)

URLS = {
    '1': 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx',
    '2': 'https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx',
}
NS_NFE = 'http://www.portalfiscal.inf.br/nfe'
NS_WSDL = 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe'
ESPERA_SEM_NOVIDADE = timedelta(hours=1)       # cStat 137 / 656
LOTES_POR_SINCRONIZACAO = 40                   # 40 × 50 = 2.000 documentos por rodada
TIMEOUT = (10, 60)
EVENTO_CANCELAMENTO = '110111'
SITUACOES = {'1': 'AUTORIZADA', '2': 'DENEGADA', '3': 'CANCELADA'}


class SefazErro(Exception):
    """A SEFAZ não respondeu ou respondeu algo que não dá para ler."""


# ── Configuração ────────────────────────────────────────────────────────────
def _cfg(nome, padrao=''):
    return (getattr(settings, nome, '') or padrao)


def _entradas_configuradas():
    """SEFAZ_CNPJS → [(cnpj, sigla da UF ou '')]. Aceita "CNPJ" ou "CNPJ:UF"."""
    bruto = _cfg('SEFAZ_CNPJS')
    if isinstance(bruto, (list, tuple)):
        bruto = ','.join(bruto)
    entradas = []
    for item in str(bruto).split(','):
        numero, _, sigla = item.partition(':')
        digitos = receita.so_digitos(numero)
        if receita.valido(digitos) and digitos not in [c for c, _ in entradas]:
            entradas.append((digitos, sigla.strip().upper()))
    return entradas


def cnpjs_monitorados():
    return [c for c, _ in _entradas_configuradas()]


def _pfx():
    b64 = _cfg('SEFAZ_CERTIFICADO_B64')
    if b64:
        return base64.b64decode(b64)
    caminho = _cfg('SEFAZ_CERTIFICADO')
    if caminho and os.path.exists(caminho):
        with open(caminho, 'rb') as arquivo:
            return arquivo.read()
    return None


def configurado():
    return bool(_pfx() and _cfg('SEFAZ_CERTIFICADO_SENHA') and cnpjs_monitorados())


def _carregar_certificado():
    from cryptography.hazmat.primitives.serialization import pkcs12
    pfx = _pfx()
    if not pfx:
        raise SefazErro('Certificado A1 não configurado (SEFAZ_CERTIFICADO).')
    try:
        return pkcs12.load_key_and_certificates(pfx, str(_cfg('SEFAZ_CERTIFICADO_SENHA')).encode())
    except Exception as exc:  # noqa: BLE001
        raise SefazErro(f'Não deu para abrir o certificado (senha ou arquivo errado): {exc}') from exc


# Código IBGE de cada UF — o que a SEFAZ pede em cUFAutor.
CODIGOS_UF = {
    'RO': '11', 'AC': '12', 'AM': '13', 'RR': '14', 'PA': '15', 'AP': '16', 'TO': '17', 'MA': '21', 'PI': '22',
    'CE': '23', 'RN': '24', 'PB': '25', 'PE': '26', 'AL': '27', 'SE': '28', 'BA': '29', 'MG': '31', 'ES': '32',
    'RJ': '33', 'SP': '35', 'PR': '41', 'SC': '42', 'RS': '43', 'MS': '50', 'MT': '51', 'GO': '52', 'DF': '53',
}


def uf_do_cnpj(cnpj):
    """Código IBGE da UF do CNPJ: a escrita em SEFAZ_CNPJS ("CNPJ:UF"), senão a do
    cadastro da Receita, senão SEFAZ_UF. As APIs públicas da Receita limitam as
    consultas (429), então com muitas filiais a UF na configuração é o caminho certo.
    """
    sigla = dict(_entradas_configuradas()).get(receita.so_digitos(cnpj), '')
    if sigla in CODIGOS_UF:
        return CODIGOS_UF[sigla]
    try:
        dados = receita.buscar(cnpj)
    except receita.CnpjIndisponivel:
        dados = None
    sigla = (dados or {}).get('uf', '').upper()
    return CODIGOS_UF.get(sigla) or str(_cfg('SEFAZ_UF', '32'))


def cnpj_do_certificado(cert=None):
    """O CNPJ do titular: o e-CNPJ da ICP-Brasil traz no nome ("RAZÃO SOCIAL:CNPJ")."""
    if cert is None:
        try:
            cert = _carregar_certificado()[1]
        except SefazErro:
            return ''
    from cryptography.x509.oid import NameOID
    for atributo in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME):
        digitos = receita.so_digitos(str(atributo.value).rsplit(':', 1)[-1])
        if receita.valido(digitos):
            return digitos
    return ''


def mesma_raiz(cnpj, cnpj_certificado):
    """Sem CNPJ legível no certificado, não bloqueia: a SEFAZ é quem decide."""
    return not cnpj_certificado or cnpj[:8] == cnpj_certificado[:8]


def info_do_certificado():
    """Titular e validade do certificado, para a tela avisar antes de vencer."""
    try:
        _chave, cert, _cadeia = _carregar_certificado()
    except SefazErro as exc:
        return {'ok': False, 'erro': str(exc)}
    from cryptography.x509.oid import NameOID
    nomes = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    validade = cert.not_valid_after_utc
    return {'ok': True, 'titular': nomes[0].value if nomes else '', 'validade': validade,
            'cnpj': cnpj_do_certificado(cert),
            'vencido': validade <= timezone.now(),
            'dias_restantes': (validade - timezone.now()).days}


@contextlib.contextmanager
def _arquivos_pem():
    """O certificado em PEM, em arquivos temporários (o requests pede caminho)."""
    from cryptography.hazmat.primitives import serialization
    chave, cert, cadeia = _carregar_certificado()
    pasta = tempfile.mkdtemp(prefix='sefaz-')
    caminho_cert, caminho_chave = os.path.join(pasta, 'cert.pem'), os.path.join(pasta, 'chave.pem')
    try:
        with open(caminho_cert, 'wb') as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
            for extra in cadeia or []:
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


# ── O pedido e a resposta ───────────────────────────────────────────────────
def envelope(cnpj, ult_nsu, uf=None, ambiente=None):
    uf = uf or _cfg('SEFAZ_UF', '32')
    ambiente = ambiente or _cfg('SEFAZ_AMBIENTE', '1')
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        f'<soap12:Body><nfeDistDFeInteresse xmlns="{NS_WSDL}"><nfeDadosMsg>'
        f'<distDFeInt xmlns="{NS_NFE}" versao="1.01"><tpAmb>{ambiente}</tpAmb><cUFAutor>{uf}</cUFAutor>'
        f'<CNPJ>{cnpj}</CNPJ><distNSU><ultNSU>{int(ult_nsu):015d}</ultNSU></distNSU></distDFeInt>'
        '</nfeDadosMsg></nfeDistDFeInteresse></soap12:Body></soap12:Envelope>'
    )


def _transporte_https(corpo):
    import requests
    with _arquivos_pem() as (cert, chave):
        resposta = requests.post(
            URLS.get(str(_cfg('SEFAZ_AMBIENTE', '1')), URLS['1']), data=corpo.encode('utf-8'),
            headers={'Content-Type': 'application/soap+xml; charset=utf-8'},
            cert=(cert, chave), timeout=TIMEOUT)
    if resposta.status_code != 200:
        raise SefazErro(f'SEFAZ respondeu HTTP {resposta.status_code}: {resposta.text[:300]}')
    return resposta.content


def ler_resposta(conteudo):
    """retDistDFeInt → {'cstat', 'motivo', 'ult_nsu', 'max_nsu', 'docs': [(nsu, schema, xml)]}."""
    try:
        raiz = etree.fromstring(conteudo)
    except etree.XMLSyntaxError as exc:
        raise SefazErro(f'Resposta da SEFAZ ilegível: {exc}') from exc
    ret = raiz.find(f'.//{{{NS_NFE}}}retDistDFeInt')
    if ret is None:
        falha = raiz.find('.//{http://www.w3.org/2003/05/soap-envelope}Text')
        raise SefazErro(f'Resposta sem retDistDFeInt: {falha.text if falha is not None else conteudo[:200]!r}')

    def texto(tag):
        el = ret.find(f'{{{NS_NFE}}}{tag}')
        return (el.text or '').strip() if el is not None else ''

    docs = []
    for doc in ret.iter(f'{{{NS_NFE}}}docZip'):
        try:
            xml = gzip.decompress(base64.b64decode(doc.text or '')).decode('utf-8')
        except Exception as exc:  # noqa: BLE001
            logger.warning('docZip NSU %s ilegível: %s', doc.get('NSU'), exc)
            continue
        docs.append((int(doc.get('NSU') or 0), doc.get('schema') or '', xml))
    return {'cstat': texto('cStat'), 'motivo': texto('xMotivo'),
            'ult_nsu': int(texto('ultNSU') or 0), 'max_nsu': int(texto('maxNSU') or 0), 'docs': docs}


# ── Os documentos ───────────────────────────────────────────────────────────
def dados_da_chave(chave):
    """Modelo, série e número estão dentro da chave de acesso."""
    chave = receita.so_digitos(chave)
    if len(chave) != 44:
        return {}
    return {'modelo': chave[20:22], 'serie': str(int(chave[22:25])), 'numero': str(int(chave[25:34])),
            'emitente_cnpj': chave[6:20]}


def _sem_ns(xml):
    raiz = etree.fromstring(xml.encode('utf-8') if isinstance(xml, str) else xml)
    for el in raiz.iter():
        if isinstance(el.tag, str) and '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    return raiz


def _t(raiz, caminho):
    el = raiz.find(caminho)
    return (el.text or '').strip() if el is not None and el.text else ''


def _decimal(texto):
    try:
        return Decimal(texto or '0').quantize(Decimal('0.01'))
    except InvalidOperation:
        return Decimal('0.00')


def _data_hora(texto):
    if not texto:
        return None
    valor = parse_datetime(texto)
    if valor is None and len(texto) == 10:
        valor = parse_datetime(f'{texto}T00:00:00')
    if valor is not None and timezone.is_naive(valor):
        valor = timezone.make_aware(valor)
    return valor


def processar_documento(cnpj, nsu, schema, xml):
    """Grava um documento do lote. Devolve 'nova', 'atualizada', 'evento' ou ''."""
    from .models import NotaRecebida

    raiz = _sem_ns(xml)
    nome = raiz.tag
    if nome == 'resNFe':
        chave = _t(raiz, 'chNFe')
        dados = dict(dados_da_chave(chave),
                     emitente_cnpj=_t(raiz, 'CNPJ') or dados_da_chave(chave).get('emitente_cnpj', ''),
                     emitente_nome=_t(raiz, 'xNome')[:200], emitente_ie=_t(raiz, 'IE')[:20],
                     emissao=_data_hora(_t(raiz, 'dhEmi')), valor=_decimal(_t(raiz, 'vNF')),
                     situacao=SITUACOES.get(_t(raiz, 'cSitNFe'), 'AUTORIZADA'),
                     tipo_operacao=_t(raiz, 'tpNF')[:1])
        nota, criada = NotaRecebida.objects.get_or_create(
            chave=chave, defaults=dict(dados, cnpj_destinatario=cnpj, nsu=nsu, xml_resumo=xml))
        if not criada:
            # A nota pode ter sido cancelada depois; a situação do resumo mais novo vale.
            if nota.situacao != 'CANCELADA':
                nota.situacao = dados['situacao']
            nota.xml_resumo = xml
            nota.save(update_fields=['situacao', 'xml_resumo', 'atualizada_em'])
        return 'nova' if criada else 'atualizada'

    if nome in ('nfeProc', 'NFe'):
        inf = raiz.find('.//infNFe')
        if inf is None:
            return ''
        chave = (inf.get('Id') or '').replace('NFe', '')
        dados = dict(dados_da_chave(chave),
                     emitente_cnpj=_t(inf, 'emit/CNPJ'), emitente_nome=_t(inf, 'emit/xNome')[:200],
                     emitente_ie=_t(inf, 'emit/IE')[:20],
                     emissao=_data_hora(_t(inf, 'ide/dhEmi') or _t(inf, 'ide/dEmi')),
                     valor=_decimal(_t(inf, 'total/ICMSTot/vNF')), tipo_operacao=_t(inf, 'ide/tpNF')[:1])
        nota, criada = NotaRecebida.objects.get_or_create(
            chave=chave, defaults=dict(dados, cnpj_destinatario=cnpj, nsu=nsu, situacao='AUTORIZADA', xml_completo=xml))
        if not criada:
            nota.xml_completo = xml
            for campo, valor in dados.items():
                if valor:
                    setattr(nota, campo, valor)
            nota.save()
        return 'nova' if criada else 'atualizada'

    if nome in ('resEvento', 'procEventoNFe'):
        tipo = _t(raiz, './/tpEvento')
        chave = _t(raiz, './/chNFe')
        if tipo == EVENTO_CANCELAMENTO and chave:
            NotaRecebida.objects.filter(chave=chave).update(situacao='CANCELADA', atualizada_em=timezone.now())
            return 'evento'
        return ''
    return ''


def vincular_lancados(notas=None):
    """Liga nota da SEFAZ ↔ documento lançado (mesmo emitente e número)."""
    from .models import DocumentoPisCofins, NotaRecebida

    qs = notas if notas is not None else NotaRecebida.objects.filter(documento__isnull=True)
    ligadas = 0
    for nota in qs.filter(documento__isnull=True).exclude(numero=''):
        doc = (DocumentoPisCofins.objects
               .filter(fornecedor__cnpj=nota.emitente_cnpj, tipo='NF', notas_sefaz__isnull=True)
               .filter(numero__regex=rf'^0*{nota.numero}$').order_by('id').first())
        if doc:
            nota.documento = doc
            nota.save(update_fields=['documento', 'atualizada_em'])
            ligadas += 1
    return ligadas


# ── Sincronizar ─────────────────────────────────────────────────────────────
def sincronizar(cnpj, transporte=None, agora=None, lotes=LOTES_POR_SINCRONIZACAO):
    """Busca os documentos novos do CNPJ. Devolve o registro de sincronização atualizado.

    Respeita a espera de 1 hora depois de "nada novo": chamar antes disso não
    vai à SEFAZ (e não arrisca o bloqueio por consumo indevido).
    """
    from .models import NotaRecebida, SincronizacaoDFe

    agora = agora or timezone.now()
    transporte = transporte or _transporte_https
    sinc, _ = SincronizacaoDFe.objects.get_or_create(cnpj=cnpj)
    if sinc.proxima_consulta and sinc.proxima_consulta > agora:
        sinc.pulada = True
        return sinc
    sinc.pulada = False
    if transporte is _transporte_https:
        do_certificado = cnpj_do_certificado()
        if not mesma_raiz(cnpj, do_certificado):
            sinc.ultimo_cstat = 'RAIZ'
            sinc.ultima_mensagem = (f'O certificado é do CNPJ {receita.formatar(do_certificado)}: só consulta a mesma '
                                    f'raiz ({do_certificado[:8]}). Este CNPJ precisa do próprio certificado.')
            sinc.ultima_consulta, sinc.notas_novas = agora, 0
            sinc.save()
            return sinc
    uf = uf_do_cnpj(cnpj)
    novas = 0
    try:
        for _ in range(lotes):
            resposta = ler_resposta(transporte(envelope(cnpj, sinc.ult_nsu, uf=uf)))
            sinc.ultimo_cstat, sinc.ultima_mensagem = resposta['cstat'], resposta['motivo'][:255]
            if resposta['cstat'] == '138':
                with transaction.atomic():
                    for nsu, schema, xml in resposta['docs']:
                        try:
                            if processar_documento(cnpj, nsu, schema, xml) == 'nova':
                                novas += 1
                        except Exception as exc:  # noqa: BLE001 — um documento ruim não para o lote
                            logger.warning('Documento NSU %s do CNPJ %s não processou: %s', nsu, cnpj, exc)
                    sinc.ult_nsu = resposta['ult_nsu'] or sinc.ult_nsu
                    sinc.max_nsu = resposta['max_nsu'] or sinc.max_nsu
                    sinc.save()
                if sinc.ult_nsu >= sinc.max_nsu:
                    sinc.proxima_consulta = agora + ESPERA_SEM_NOVIDADE
                    break
                continue
            # 137 nada novo; 656 consumo indevido; outros: erro de cadastro/certificado.
            if resposta['ult_nsu']:
                sinc.ult_nsu = resposta['ult_nsu']
            if resposta['max_nsu']:
                sinc.max_nsu = resposta['max_nsu']
            sinc.proxima_consulta = agora + ESPERA_SEM_NOVIDADE
            break
    except SefazErro as exc:
        sinc.ultimo_cstat, sinc.ultima_mensagem = 'ERRO', str(exc)[:255]
        sinc.proxima_consulta = agora + timedelta(minutes=15)
    sinc.ultima_consulta = agora
    sinc.notas_novas = novas
    sinc.save()
    if novas:
        vincular_lancados(NotaRecebida.objects.filter(cnpj_destinatario=cnpj))
    return sinc


def sincronizar_todos(transporte=None, prazo_segundos=None, transporte_nfse=None):
    """Sincroniza NF-e (SEFAZ) e NFS-e (ADN) de cada CNPJ, o consultado há mais tempo primeiro.

    Com ``prazo_segundos`` (o botão da tela), para de começar CNPJ novo quando o
    prazo passa — a requisição não pode durar minutos; o que sobrar vai no
    próximo clique ou no comando agendado. Os que ficaram de fora voltam com
    ``adiada = True``. Um teste que só troca o transporte da NF-e não liga a
    NFS-e (que sairia para a internet).
    """
    import time

    from . import nfse_nacional
    from .models import SincronizacaoDFe

    incluir_nfse = transporte_nfse is not None or transporte is None
    ultimas = dict(SincronizacaoDFe.objects.values_list('cnpj', 'ultima_consulta'))
    ordem = sorted(cnpjs_monitorados(), key=lambda c: (ultimas.get(c) is not None, ultimas.get(c) or 0))
    inicio, resultado = time.monotonic(), []
    for cnpj in ordem:
        if prazo_segundos is not None and time.monotonic() - inicio > prazo_segundos:
            sinc, _ = SincronizacaoDFe.objects.get_or_create(cnpj=cnpj)
            sinc.pulada, sinc.adiada, sinc.pulada_nfse = True, True, True
            resultado.append(sinc)
            continue
        sinc = sincronizar(cnpj, transporte=transporte)
        sinc.adiada = False
        if incluir_nfse:
            depois = nfse_nacional.sincronizar(cnpj, transporte=transporte_nfse)
            for campo in ('ult_nsu_nfse', 'ultima_consulta_nfse', 'proxima_consulta_nfse', 'ultimo_status_nfse',
                          'ultima_mensagem_nfse', 'notas_novas_nfse', 'pulada_nfse'):
                setattr(sinc, campo, getattr(depois, campo, None))
        else:
            sinc.pulada_nfse, sinc.notas_novas_nfse = True, 0
        resultado.append(sinc)
    return resultado
