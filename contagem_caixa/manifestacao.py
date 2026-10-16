"""Manifestação do destinatário da NF-e e importação de nota pela chave.

**Manifestação** (NT 2012/002, evento enviado ao Ambiente Nacional, cOrgao 91):

- 210210 Ciência da operação — "a nota existe, ainda vou conferir". Libera o
  download do XML completo; não é conclusiva;
- 210200 Confirmação da operação — a mercadoria/serviço chegou;
- 210220 Desconhecimento da operação — não reconhecemos a nota;
- 210240 Operação não realizada — a nota é nossa, mas a operação não aconteceu
  (exige justificativa de 15 a 255 caracteres).

O evento vai assinado (XMLDSig, RSA-SHA1, C14N — o que o leiaute da NF-e pede)
com o mesmo certificado A1 da busca de notas (``sefaz.py``). A SEFAZ responde
135/136 quando registra; 573 é "já registrado" (duplicidade) — conta como feito.

**Importar pela chave**: ``distDFeInt`` com ``consChNFe`` pede à SEFAZ uma nota
específica (44 dígitos). Volta o resumo — ou o XML completo, se já houve
ciência. É também o jeito de buscar o XML completo de uma nota que já está no
portal só com o resumo.

Nada aqui é chamado por teste sem ``transporte`` falso: o envio é real e tem
efeito fiscal.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from django.db import transaction
from django.utils import timezone
from lxml import etree

from . import cnpj as receita
from . import sefaz

logger = logging.getLogger(__name__)

NS_NFE = sefaz.NS_NFE
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
NS_WSDL_EVENTO = 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4'
URLS_EVENTO = {
    '1': 'https://www.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx',
    '2': 'https://hom1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx',
}
DESCRICOES = {
    '210210': 'Ciencia da Operacao',
    '210200': 'Confirmacao da Operacao',
    '210220': 'Desconhecimento da Operacao',
    '210240': 'Operacao nao Realizada',
}
REGISTRADO = ('135', '136', '573')
C14N = 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'


class ManifestacaoInvalida(Exception):
    pass


# ── Montar e assinar ────────────────────────────────────────────────────────
def _dh_evento(agora=None):
    agora = timezone.localtime(agora or timezone.now()).replace(microsecond=0)
    texto = agora.strftime('%Y-%m-%dT%H:%M:%S%z')
    return texto[:-2] + ':' + texto[-2:]


def montar_evento(chave, cnpj, tipo, justificativa='', seq=1, agora=None, ambiente=None):
    """O <evento> ainda sem assinatura (elemento lxml)."""
    if tipo not in DESCRICOES:
        raise ManifestacaoInvalida('Tipo de manifestação desconhecido.')
    chave = receita.so_digitos(chave)
    if len(chave) != 44:
        raise ManifestacaoInvalida('A chave de acesso tem 44 números.')
    justificativa = ' '.join((justificativa or '').split())
    if tipo == '210240' and not (15 <= len(justificativa) <= 255):
        raise ManifestacaoInvalida('"Operação não realizada" pede uma justificativa de 15 a 255 caracteres.')
    ambiente = ambiente or str(sefaz._cfg('SEFAZ_AMBIENTE', '1'))
    id_evento = f'ID{tipo}{chave}{seq:02d}'
    evento = etree.Element(f'{{{NS_NFE}}}evento', nsmap={None: NS_NFE}, versao='1.00')
    inf = etree.SubElement(evento, f'{{{NS_NFE}}}infEvento', Id=id_evento)
    for tag, valor in (('cOrgao', '91'), ('tpAmb', ambiente), ('CNPJ', cnpj), ('chNFe', chave),
                       ('dhEvento', _dh_evento(agora)), ('tpEvento', tipo), ('nSeqEvento', str(seq)),
                       ('verEvento', '1.00')):
        etree.SubElement(inf, f'{{{NS_NFE}}}{tag}').text = valor
    det = etree.SubElement(inf, f'{{{NS_NFE}}}detEvento', versao='1.00')
    etree.SubElement(det, f'{{{NS_NFE}}}descEvento').text = DESCRICOES[tipo]
    if tipo == '210240':
        etree.SubElement(det, f'{{{NS_NFE}}}xJust').text = justificativa
    return evento


def _c14n(elemento):
    return etree.tostring(elemento, method='c14n', exclusive=False, with_comments=False)


def assinar(evento, chave_privada, certificado):
    """Assinatura XMLDSig envelopada do infEvento (RSA-SHA1, C14N 1.0)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    # O evento é assinado como documento próprio: é assim que a SEFAZ o lê
    # dentro de nfeDadosMsg.
    evento = etree.fromstring(etree.tostring(evento))
    inf = evento.find(f'{{{NS_NFE}}}infEvento')
    digest = base64.b64encode(hashlib.sha1(_c14n(inf)).digest()).decode()

    ds = lambda tag: f'{{{NS_DS}}}{tag}'  # noqa: E731
    assinatura = etree.SubElement(evento, ds('Signature'), nsmap={None: NS_DS})
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
    key_info = etree.SubElement(assinatura, ds('KeyInfo'))
    x509 = etree.SubElement(key_info, ds('X509Data'))
    der = certificado.public_bytes(serialization.Encoding.DER)
    etree.SubElement(x509, ds('X509Certificate')).text = base64.b64encode(der).decode()
    return evento


def verificar_assinatura(evento):
    """Confere digest e assinatura (para o teste e para o log)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    # Como a SEFAZ lê: o evento sozinho, sem os namespaces do envelope SOAP.
    evento = etree.fromstring(etree.tostring(evento))
    etree.cleanup_namespaces(evento)
    assinatura = evento.find(f'{{{NS_DS}}}Signature')
    inf = evento.find(f'{{{NS_NFE}}}infEvento')
    info = assinatura.find(f'{{{NS_DS}}}SignedInfo')
    digest = info.find(f'.//{{{NS_DS}}}DigestValue').text
    evento.remove(assinatura)
    if base64.b64encode(hashlib.sha1(_c14n(inf)).digest()).decode() != digest:
        return False
    cert = x509.load_der_x509_certificate(base64.b64decode(assinatura.find(f'.//{{{NS_DS}}}X509Certificate').text))
    try:
        cert.public_key().verify(base64.b64decode(assinatura.find(f'{{{NS_DS}}}SignatureValue').text),
                                 _c14n(info), padding.PKCS1v15(), hashes.SHA1())
    except Exception:  # noqa: BLE001
        return False
    return True


def envelope_evento(evento_assinado, id_lote):
    corpo = etree.tostring(evento_assinado).decode()
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        f'<soap12:Body><nfeDadosMsg xmlns="{NS_WSDL_EVENTO}">'
        f'<envEvento xmlns="{NS_NFE}" versao="1.00"><idLote>{id_lote}</idLote>{corpo}</envEvento>'
        '</nfeDadosMsg></soap12:Body></soap12:Envelope>'
    )


def ler_retorno(conteudo):
    try:
        raiz = etree.fromstring(conteudo)
    except etree.XMLSyntaxError as exc:
        raise sefaz.SefazErro(f'Resposta da SEFAZ ilegível: {exc}') from exc
    ret = raiz.find(f'.//{{{NS_NFE}}}retEnvEvento')
    if ret is None:
        falha = raiz.find('.//{http://www.w3.org/2003/05/soap-envelope}Text')
        raise sefaz.SefazErro(f'Resposta sem retEnvEvento: {falha.text if falha is not None else conteudo[:200]!r}')

    def t(el, tag):
        achado = el.find(f'{{{NS_NFE}}}{tag}') if el is not None else None
        return (achado.text or '').strip() if achado is not None and achado.text else ''

    inf = ret.find(f'.//{{{NS_NFE}}}retEvento/{{{NS_NFE}}}infEvento')
    if inf is None:  # lote recusado inteiro
        return {'cstat': t(ret, 'cStat'), 'motivo': t(ret, 'xMotivo'), 'protocolo': ''}
    return {'cstat': t(inf, 'cStat'), 'motivo': t(inf, 'xMotivo'), 'protocolo': t(inf, 'nProt')}


def _transporte_evento(corpo):
    import requests
    with sefaz._arquivos_pem() as (cert, chave):
        resposta = requests.post(
            URLS_EVENTO.get(str(sefaz._cfg('SEFAZ_AMBIENTE', '1')), URLS_EVENTO['1']), data=corpo.encode('utf-8'),
            headers={'Content-Type': 'application/soap+xml; charset=utf-8'},
            cert=(cert, chave), timeout=sefaz.TIMEOUT)
    if resposta.status_code != 200:
        raise sefaz.SefazErro(f'SEFAZ respondeu HTTP {resposta.status_code}: {resposta.text[:300]}')
    return resposta.content


# ── Enviar ──────────────────────────────────────────────────────────────────
def situacao_da_nota(nota):
    """A última manifestação registrada (ou None)."""
    return nota.manifestacoes.filter(sucesso=True).order_by('-enviado_em', '-id').first()


def manifestar(nota, tipo, justificativa='', usuario=None, transporte=None, certificado=None, agora=None):
    """Envia o evento e grava a tentativa. Devolve a ManifestacaoNFe."""
    from .models import ManifestacaoNFe

    if nota.tipo_documento != 'NFE':
        raise ManifestacaoInvalida('Só NF-e tem manifestação do destinatário (NFS-e não tem).')
    if nota.situacao == 'CANCELADA':
        raise ManifestacaoInvalida('A nota foi cancelada pelo emitente: não cabe manifestação.')
    feita = situacao_da_nota(nota)
    conclusivas = ('210200', '210220', '210240')
    if feita and feita.tipo == tipo:
        raise ManifestacaoInvalida(f'Esta nota já tem "{feita.get_tipo_display()}" registrada.')
    if tipo == '210210' and feita and feita.tipo in conclusivas:
        raise ManifestacaoInvalida('A nota já tem manifestação conclusiva: não cabe mais ciência.')
    if certificado is None:
        chave_privada, cert, _cadeia = sefaz._carregar_certificado()
    else:
        chave_privada, cert = certificado
    if transporte is None:
        do_certificado = sefaz.cnpj_do_certificado(cert)
        if not sefaz.mesma_raiz(nota.cnpj_destinatario, do_certificado):
            raise ManifestacaoInvalida('O certificado é de outra empresa (outra raiz de CNPJ).')
    transporte = transporte or _transporte_evento
    evento = assinar(montar_evento(nota.chave, nota.cnpj_destinatario, tipo, justificativa, agora=agora),
                     chave_privada, cert)
    id_lote = timezone.now().strftime('%y%m%d%H%M%S') + f'{nota.pk % 1000:03d}'
    registro = ManifestacaoNFe(nota=nota, tipo=tipo, justificativa=' '.join((justificativa or '').split())[:255],
                               enviado_por=usuario, xml_enviado=etree.tostring(evento).decode())
    try:
        bruto = transporte(envelope_evento(evento, id_lote))
        resposta = ler_retorno(bruto)
        registro.xml_retorno = bruto.decode('utf-8', errors='replace') if isinstance(bruto, bytes) else str(bruto)
        registro.cstat, registro.motivo = resposta['cstat'][:10], resposta['motivo'][:255]
        registro.protocolo = resposta['protocolo'][:20]
        registro.sucesso = resposta['cstat'] in REGISTRADO
    except sefaz.SefazErro as exc:
        registro.cstat, registro.motivo = 'ERRO', str(exc)[:255]
    registro.save()
    return registro


# ── Importar pela chave ─────────────────────────────────────────────────────
def envelope_chave(cnpj, chave, uf=None, ambiente=None):
    uf = uf or sefaz._cfg('SEFAZ_UF', '32')
    ambiente = ambiente or sefaz._cfg('SEFAZ_AMBIENTE', '1')
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        f'<soap12:Body><nfeDistDFeInteresse xmlns="{sefaz.NS_WSDL}"><nfeDadosMsg>'
        f'<distDFeInt xmlns="{NS_NFE}" versao="1.01"><tpAmb>{ambiente}</tpAmb><cUFAutor>{uf}</cUFAutor>'
        f'<CNPJ>{cnpj}</CNPJ><consChNFe><chNFe>{chave}</chNFe></consChNFe></distDFeInt>'
        '</nfeDadosMsg></nfeDistDFeInteresse></soap12:Body></soap12:Envelope>'
    )


def importar_por_chave(chave, cnpj, transporte=None):
    """Busca a nota na SEFAZ. Devolve (nota ou None, mensagem)."""
    from .models import NotaRecebida

    chave = receita.so_digitos(chave)
    if len(chave) != 44:
        raise ManifestacaoInvalida('A chave de acesso tem 44 números.')
    if chave[20:22] not in ('55', '65'):
        raise ManifestacaoInvalida('Esta chave não é de NF-e (modelo 55).')
    cnpj = receita.so_digitos(cnpj)
    if cnpj not in sefaz.cnpjs_monitorados():
        raise ManifestacaoInvalida('Escolha um dos CNPJs da empresa acompanhados na SEFAZ.')
    if transporte is None and not sefaz.configurado():
        raise ManifestacaoInvalida('A busca na SEFAZ não está ligada (falta o certificado A1).')
    transporte = transporte or sefaz._transporte_https
    resposta = sefaz.ler_resposta(transporte(envelope_chave(cnpj, chave, uf=sefaz.uf_do_cnpj(cnpj)
                                                            if transporte is sefaz._transporte_https else None)))
    if resposta['cstat'] != '138':
        return NotaRecebida.objects.filter(chave=chave).first(), f"SEFAZ {resposta['cstat']}: {resposta['motivo']}"
    with transaction.atomic():
        for nsu, schema, xml in resposta['docs']:
            try:
                sefaz.processar_documento(cnpj, nsu, schema, xml)
            except Exception as exc:  # noqa: BLE001
                logger.warning('Documento da chave %s não processou: %s', chave, exc)
    nota = NotaRecebida.objects.filter(chave=chave).first()
    if nota is not None:
        sefaz.vincular_lancados(NotaRecebida.objects.filter(pk=nota.pk))
        nota.refresh_from_db()
    if nota is None:
        return None, 'A SEFAZ respondeu, mas sem a nota.'
    return nota, 'XML completo baixado.' if nota.xml_completo else 'Resumo baixado (o XML completo vem depois da ciência).'
