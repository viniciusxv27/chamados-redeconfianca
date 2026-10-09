"""PIS/Cofins: notas emitidas contra o CNPJ (SEFAZ, NF-e Distribuição DFe).

Pedido: "puxe da API da Receita ou de alguma outra todas as notas/documentos
emitidos no CNPJ da empresa para ter essa visão".

A SEFAZ aqui é um dublê que responde no formato oficial (retDistDFeInt com
docZip em gzip+base64); o certificado A1 é gerado na hora (autoassinado). O
que este teste cobre:

- o pedido (envelope SOAP) e a leitura da resposta;
- sincronizar: lotes até ultNSU == maxNSU, resumo (resNFe), XML completo
  (procNFe), cancelamento (resEvento 110111), número/série pela chave;
- a espera de 1 hora depois de "nada novo" (137) e de consumo indevido (656),
  erro de comunicação, e não ir à SEFAZ antes da hora;
- ligar nota ↔ documento já lançado;
- certificado: abrir o .pfx, titular e validade, PEM temporário apagado;
- a tela: sem configuração explica como ligar; configurada lista as notas,
  conta "a lançar", lança a partir da nota (o XML vira o arquivo), marca fora
  do PIS/Cofins, e a visão dos lançados avisa o que falta.

Caches em memória, transação desfeita no fim; arquivos que subirem são apagados.
"""
import base64
import gzip
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sf'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sf-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from contagem_caixa import cnpj as receita
from contagem_caixa import sefaz
from contagem_caixa.models import DocumentoPisCofins, FornecedorPisCofins, NotaRecebida, SincronizacaoDFe

User = get_user_model()
D = Decimal
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def cnpj_de(base12):
    d = [int(c) for c in base12]
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(d[i] * pesos[i] for i in range(tamanho)) % 11
        d.append(0 if resto < 2 else 11 - resto)
    return ''.join(map(str, d))


NOSSO, FORN_A, FORN_B = cnpj_de('393939390001'), cnpj_de('112223330001'), cnpj_de('445556660001')


def chave(emitente, numero, serie=1, modelo='55'):
    base = f'32{"1905"}{emitente}{modelo}{serie:03d}{numero:09d}1{12345678:08d}'
    pesos = [2, 3, 4, 5, 6, 7, 8, 9] * 6
    soma = sum(int(c) * pesos[i] for i, c in enumerate(reversed(base)))
    dv = 11 - soma % 11
    return base + str(0 if dv >= 10 else dv)


CH1, CH2, CH3 = chave(FORN_A, 123), chave(FORN_B, 4567, serie=2), chave(FORN_A, 124)
NS = 'http://www.portalfiscal.inf.br/nfe'


def res_nfe(ch, emit, nome, valor, dia, sit='1'):
    return (f'<resNFe xmlns="{NS}" versao="1.01"><chNFe>{ch}</chNFe><CNPJ>{emit}</CNPJ><xNome>{nome}</xNome>'
            f'<IE>123456</IE><dhEmi>2019-05-{dia:02d}T10:00:00-03:00</dhEmi><tpNF>1</tpNF><vNF>{valor}</vNF>'
            f'<digVal>x</digVal><dhRecbto>2019-05-{dia:02d}T10:01:00-03:00</dhRecbto><nProt>1</nProt>'
            f'<cSitNFe>{sit}</cSitNFe></resNFe>')


def proc_nfe(ch, emit, nome, valor):
    return (f'<nfeProc xmlns="{NS}" versao="4.00"><NFe><infNFe Id="NFe{ch}" versao="4.00">'
            f'<ide><nNF>{int(ch[25:34])}</nNF><dhEmi>2019-05-03T09:00:00-03:00</dhEmi><tpNF>1</tpNF></ide>'
            f'<emit><CNPJ>{emit}</CNPJ><xNome>{nome}</xNome><IE>999</IE></emit><dest><CNPJ>{NOSSO}</CNPJ></dest>'
            f'<total><ICMSTot><vNF>{valor}</vNF></ICMSTot></total></infNFe></NFe></nfeProc>')


def cancelamento(ch):
    return (f'<resEvento xmlns="{NS}" versao="1.01"><cOrgao>32</cOrgao><CNPJ>{FORN_A}</CNPJ><chNFe>{ch}</chNFe>'
            f'<dhEvento>2019-05-10T10:00:00-03:00</dhEvento><tpEvento>110111</tpEvento><nSeqEvento>1</nSeqEvento>'
            f'<xEvento>Cancelamento</xEvento></resEvento>')


def resposta(cstat, motivo, ult, maximo, docs=()):
    lote = ''
    if docs:
        lote = '<loteDistDFeInt>' + ''.join(
            f'<docZip NSU="{nsu:015d}" schema="{schema}">{base64.b64encode(gzip.compress(xml.encode())).decode()}</docZip>'
            for nsu, schema, xml in docs) + '</loteDistDFeInt>'
    return (f'<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
            f'<soap:Body><nfeDistDFeInteresseResponse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">'
            f'<nfeDistDFeInteresseResult><retDistDFeInt xmlns="{NS}" versao="1.01"><tpAmb>1</tpAmb>'
            f'<verAplic>1</verAplic><cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo>'
            f'<dhResp>2019-05-20T10:00:00-03:00</dhResp><ultNSU>{ult:015d}</ultNSU><maxNSU>{maximo:015d}</maxNSU>'
            f'{lote}</retDistDFeInt></nfeDistDFeInteresseResult></nfeDistDFeInteresseResponse></soap:Body>'
            f'</soap:Envelope>').encode()


NS_NFSE = 'http://www.sped.fazenda.gov.br/nfse'


def nfse_xml(chave, prest, nome, toma, valor, compet, numero, descricao):
    return (f'<NFSe versao="1.00" xmlns="{NS_NFSE}"><infNFSe Id="NFS{chave}"><nNFSe>{numero}</nNFSe>'
            f'<xTribNac>Serviço</xTribNac><dhProc>{compet}T10:00:00-03:00</dhProc>'
            f'<emit><CNPJ>{prest}</CNPJ><xNome>{nome}</xNome><email>pessoal@exemplo-teste.local</email></emit>'
            f'<valores><vLiq>{valor}</vLiq></valores><DPS versao="1.00"><infDPS Id="DPS1"><dhEmi>{compet}T09:00:00-03:00</dhEmi>'
            f'<serie>900</serie><dCompet>{compet}</dCompet><prest><CNPJ>{prest}</CNPJ></prest><toma><CNPJ>{toma}</CNPJ></toma>'
            f'<serv><cServ><xDescServ>{descricao}</xDescServ></cServ></serv>'
            f'<valores><vServPrest><vServ>{valor}</vServ></vServPrest></valores></infDPS></DPS></infNFSe></NFSe>')


def evento_cancelamento_nfse(chave):
    return (f'<evento versao="1.00" xmlns="{NS_NFSE}"><infEvento Id="EVT{chave}"><pedRegEvento><infPedReg>'
            f'<chNFSe>{chave}</chNFSe><e101101><xDesc>Cancelamento de NFS-e</xDesc></e101101></infPedReg></pedRegEvento>'
            f'</infEvento></evento>')


def lote_adn(docs, status='DOCUMENTOS_LOCALIZADOS'):
    return {'StatusProcessamento': status, 'Erros': [] if docs else [{'Codigo': 'E2220', 'Descricao': 'Nenhum documento localizado'}],
            'LoteDFe': [{'NSU': nsu, 'ChaveAcesso': ch, 'TipoDocumento': tipo,
                         'ArquivoXml': base64.b64encode(gzip.compress(xml.encode())).decode()} for nsu, tipo, ch, xml in docs]}


class AdnFalso:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.pedidos = []

    def __call__(self, cnpj, nsu):
        self.pedidos.append((cnpj, nsu))
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class SefazFalsa:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.pedidos = []

    def __call__(self, corpo):
        self.pedidos.append(corpo)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


subidos = []
marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== PEDIDO E RESPOSTA ==')
    env = sefaz.envelope(NOSSO, 42, uf='32', ambiente='1')
    t('envelope SOAP com CNPJ, UF, ambiente e ultNSU de 15 dígitos',
      f'<CNPJ>{NOSSO}</CNPJ>' in env and '<cUFAutor>32</cUFAutor>' in env and '<tpAmb>1</tpAmb>' in env
      and '<ultNSU>000000000000042</ultNSU>' in env and 'versao="1.01"' in env and 'nfeDistDFeInteresse' in env)
    lido = sefaz.ler_resposta(resposta('138', 'Documento localizado', 2, 5,
                                       [(1, 'resNFe_v1.01.xsd', res_nfe(CH1, FORN_A, 'ZZ A', '100.00', 2))]))
    t('lê cStat, NSUs e descompacta o docZip', lido['cstat'] == '138' and lido['ult_nsu'] == 2 and lido['max_nsu'] == 5
      and lido['docs'][0][0] == 1 and '<chNFe>' in lido['docs'][0][2], lido)
    t('dados da chave', sefaz.dados_da_chave(CH2) == {'modelo': '55', 'serie': '2', 'numero': '4567', 'emitente_cnpj': FORN_B},
      sefaz.dados_da_chave(CH2))
    try:
        sefaz.ler_resposta(b'<html>erro</html>')
        t('resposta estranha vira SefazErro', False)
    except sefaz.SefazErro:
        t('resposta estranha vira SefazErro', True)

    print('== SINCRONIZAR ==')
    agora = timezone.make_aware(datetime(2019, 5, 20, 10, 0))
    falsa = SefazFalsa([
        resposta('138', 'Documento localizado', 2, 4, [
            (1, 'resNFe_v1.01.xsd', res_nfe(CH1, FORN_A, 'ZZ FORNECEDOR A LTDA', '1500.00', 2)),
            (2, 'resNFe_v1.01.xsd', res_nfe(CH2, FORN_B, 'ZZ FORNECEDOR B SA', '89.90', 5)),
        ]),
        resposta('138', 'Documento localizado', 4, 4, [
            (3, 'resNFe_v1.01.xsd', res_nfe(CH3, FORN_A, 'ZZ FORNECEDOR A LTDA', '300.00', 7)),
            (4, 'resEvento_1.01.xsd', cancelamento(CH3)),
        ]),
    ])
    sinc = sefaz.sincronizar(NOSSO, transporte=falsa, agora=agora)
    t('dois lotes até ultNSU == maxNSU', len(falsa.pedidos) == 2 and '<ultNSU>000000000000002</ultNSU>' in falsa.pedidos[1])
    t('3 notas novas e NSU guardado', sinc.notas_novas == 3 and sinc.ult_nsu == 4 and sinc.max_nsu == 4, (sinc.notas_novas, sinc.ult_nsu))
    n1 = NotaRecebida.objects.get(chave=CH1)
    t('resumo gravado (emitente, valor, emissão, número/série pela chave)',
      n1.emitente_cnpj == FORN_A and n1.emitente_nome == 'ZZ FORNECEDOR A LTDA' and n1.valor == D('1500.00')
      and n1.emissao.date() == date(2019, 5, 2) and n1.numero == '123' and n1.serie == '1' and n1.cnpj_destinatario == NOSSO)
    t('cancelamento aplicado', NotaRecebida.objects.get(chave=CH3).situacao == 'CANCELADA')
    t('próxima consulta só daqui a 1 hora', sinc.proxima_consulta == agora + timedelta(hours=1), sinc.proxima_consulta)

    falsa2 = SefazFalsa([resposta('137', 'Nenhum documento localizado', 4, 4)])
    sinc = sefaz.sincronizar(NOSSO, transporte=falsa2, agora=agora + timedelta(minutes=30))
    t('antes de 1 hora não vai à SEFAZ', sinc.pulada and not falsa2.pedidos)
    sinc = sefaz.sincronizar(NOSSO, transporte=falsa2, agora=agora + timedelta(minutes=61))
    t('depois de 1 hora consulta; 137 marca nova espera', not sinc.pulada and sinc.ultimo_cstat == '137'
      and sinc.proxima_consulta == agora + timedelta(minutes=121))

    falsa3 = SefazFalsa([resposta('138', 'Documento localizado', 5, 5, [
        (5, 'procNFe_v4.00.xsd', proc_nfe(CH1, FORN_A, 'ZZ FORNECEDOR A LTDA', '1500.00'))])])
    sefaz.sincronizar(NOSSO, transporte=falsa3, agora=agora + timedelta(hours=3))
    n1.refresh_from_db()
    t('XML completo (procNFe) completa a nota existente', n1.xml_completo.startswith('<nfeProc') and n1.emitente_ie == '999'
      and NotaRecebida.objects.filter(chave=CH1).count() == 1)

    falsa4 = SefazFalsa([resposta('656', 'Rejeicao: Consumo Indevido', 5, 5)])
    sinc = sefaz.sincronizar(NOSSO, transporte=falsa4, agora=agora + timedelta(hours=5))
    t('656 (consumo indevido) espera 1 hora', sinc.ultimo_cstat == '656' and sinc.proxima_consulta == agora + timedelta(hours=6))
    falsa5 = SefazFalsa([sefaz.SefazErro('SEFAZ respondeu HTTP 403')])
    sinc = sefaz.sincronizar(NOSSO, transporte=falsa5, agora=agora + timedelta(hours=7))
    t('erro de comunicação: registra e tenta em 15 min', sinc.ultimo_cstat == 'ERRO' and '403' in sinc.ultima_mensagem
      and sinc.proxima_consulta == agora + timedelta(hours=7, minutes=15))

    print('== CERTIFICADO ==')
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import NameOID
    chave_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'ZZ EMPRESA TESTE:{NOSSO}')])
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chave_priv.public_key())
            .serial_number(1).not_valid_before(datetime(2020, 1, 1))
            .not_valid_after(datetime.now() + timedelta(days=20)).sign(chave_priv, hashes.SHA256()))
    pfx = pkcs12.serialize_key_and_certificates(b'zz', chave_priv, cert, None, BestAvailableEncryption(b'senha-teste'))
    antes = {k: getattr(settings, k, '') for k in ('SEFAZ_CERTIFICADO_B64', 'SEFAZ_CERTIFICADO_SENHA', 'SEFAZ_CNPJS', 'SEFAZ_CERTIFICADO')}
    settings.SEFAZ_CERTIFICADO = ''
    settings.SEFAZ_CERTIFICADO_B64, settings.SEFAZ_CERTIFICADO_SENHA = '', ''
    settings.SEFAZ_CNPJS = ''
    t('sem certificado: desligado', not sefaz.configurado())
    settings.SEFAZ_CERTIFICADO_B64 = base64.b64encode(pfx).decode()
    settings.SEFAZ_CERTIFICADO_SENHA = 'senha-teste'
    settings.SEFAZ_CNPJS = f'{receita.formatar(NOSSO)}, 123'
    t('com certificado, senha e CNPJ: ligado (CNPJ inválido ignorado)', sefaz.configurado() and sefaz.cnpjs_monitorados() == [NOSSO])
    info = sefaz.info_do_certificado()
    t('titular e validade do certificado', info['ok'] and 'ZZ EMPRESA TESTE' in info['titular'] and 18 <= info['dias_restantes'] <= 20, info)
    with sefaz._arquivos_pem() as (c_pem, k_pem):
        existe = os.path.exists(c_pem) and os.path.exists(k_pem)
        permissao = oct(os.stat(k_pem).st_mode & 0o777)
        conteudo = open(c_pem).read()
    t('PEM temporário criado (chave só para o dono) e apagado', existe and permissao == '0o600'
      and 'BEGIN CERTIFICATE' in conteudo and not os.path.exists(c_pem) and not os.path.exists(k_pem), permissao)
    t('CNPJ do titular lido do certificado', sefaz.cnpj_do_certificado() == NOSSO, sefaz.cnpj_do_certificado())
    OUTRA_RAIZ, FILIAL = cnpj_de('585858580001'), NOSSO[:8] + '0002'
    FILIAL = cnpj_de(FILIAL)
    t('mesma raiz: filial sim, outra empresa não', sefaz.mesma_raiz(FILIAL, NOSSO) and not sefaz.mesma_raiz(OUTRA_RAIZ, NOSSO))
    sinc = sefaz.sincronizar(OUTRA_RAIZ, agora=agora + timedelta(days=2))
    t('CNPJ de outra raiz: não consulta e explica', sinc.ultimo_cstat == 'RAIZ' and 'próprio certificado' in sinc.ultima_mensagem,
      sinc.ultima_mensagem)

    print('== UF DE CADA CNPJ ==')
    buscar_original = receita.buscar
    ufs = {NOSSO: 'ES', FILIAL: 'RJ', OUTRA_RAIZ: 'SP'}
    receita.buscar = lambda v: {'cnpj': v, 'razao_social': 'ZZ', 'uf': ufs.get(receita.so_digitos(v), '')}
    t('ES 32, RJ 33, SP 35', (sefaz.uf_do_cnpj(NOSSO), sefaz.uf_do_cnpj(FILIAL), sefaz.uf_do_cnpj(OUTRA_RAIZ)) == ('32', '33', '35'))
    falsa_rj = SefazFalsa([resposta('137', 'Nenhum documento localizado', 0, 0)])
    sefaz.sincronizar(FILIAL, transporte=falsa_rj, agora=agora)
    t('o pedido da filial do RJ vai com cUFAutor 33', '<cUFAutor>33</cUFAutor>' in falsa_rj.pedidos[0])
    def fora_do_ar(v):
        raise receita.CnpjIndisponivel('fora')
    receita.buscar = fora_do_ar
    settings.SEFAZ_UF = '32'
    t('Receita fora: usa SEFAZ_UF', sefaz.uf_do_cnpj(FILIAL) == '32')
    cnpjs_antes = settings.SEFAZ_CNPJS
    settings.SEFAZ_CNPJS = f'{receita.formatar(NOSSO)}:ES, {receita.formatar(FILIAL)}:rj ,{OUTRA_RAIZ}:SP,{NOSSO}:RJ'
    t('"CNPJ:UF" na configuração: UF de cada um, sem a Receita',
      sefaz.cnpjs_monitorados() == [NOSSO, FILIAL, OUTRA_RAIZ]
      and (sefaz.uf_do_cnpj(NOSSO), sefaz.uf_do_cnpj(FILIAL), sefaz.uf_do_cnpj(OUTRA_RAIZ)) == ('32', '33', '35'))
    settings.SEFAZ_CNPJS = f'{NOSSO}:ES,{FILIAL}:RJ'
    SincronizacaoDFe.objects.filter(cnpj__in=[NOSSO, FILIAL]).update(proxima_consulta=None)
    falsa_t = SefazFalsa([resposta('137', 'Nenhum documento localizado', 0, 0)] * 2)
    todos = sefaz.sincronizar_todos(transporte=falsa_t, prazo_segundos=-1)
    t('prazo esgotado: os CNPJs ficam para depois, sem ir à SEFAZ',
      all(s.adiada for s in todos) and not falsa_t.pedidos and len(todos) == 2)
    todos = sefaz.sincronizar_todos(transporte=falsa_t)
    t('sem prazo (comando agendado): consulta todos', len(falsa_t.pedidos) == 2 and not any(s.adiada for s in todos))
    settings.SEFAZ_CNPJS = cnpjs_antes
    receita.buscar = buscar_original

    settings.SEFAZ_CERTIFICADO_SENHA = 'errada'
    t('senha errada: erro claro', not sefaz.info_do_certificado()['ok'] and 'senha' in sefaz.info_do_certificado()['erro'])
    settings.SEFAZ_CERTIFICADO_SENHA = 'senha-teste'

    print('== NFS-e (AMBIENTE NACIONAL) ==')
    from contagem_caixa import nfse_nacional
    CHS1, CHS2, CHS3 = '3205002' + FORN_B + '000000000000123' + '0' * 14, '3205002' + FORN_A + '0' * 29, '3205002' + NOSSO + '1' * 29
    CHS1, CHS2, CHS3 = CHS1[:50].ljust(50, '0'), CHS2[:50].ljust(50, '1'), CHS3[:50].ljust(50, '2')
    adn = AdnFalso([
        lote_adn([(1, 'NFSE', CHS1, nfse_xml(CHS1, FORN_B, 'ZZ MANUTENCAO ME', NOSSO, '300.00', '2019-04-28', 123, 'Manutenção de câmeras e alarme')),
                  (2, 'NFSE', CHS2, nfse_xml(CHS2, FORN_A, 'ZZ LIMPEZA LTDA', NOSSO, '850.50', '2019-05-15', 77, 'Limpeza pós-obra')),
                  (3, 'NFSE', CHS3, nfse_xml(CHS3, NOSSO, 'NOS MESMOS', FORN_A, '99.00', '2019-05-16', 5, 'Serviço que nós prestamos'))]),
    ])
    sinc = nfse_nacional.sincronizar(NOSSO, transporte=adn, agora=agora)
    t('NFS-e: lote lido, só as que a empresa tomou', sinc.notas_novas_nfse == 2 and sinc.ult_nsu_nfse == 3
      and not NotaRecebida.objects.filter(chave=CHS3).exists(), (sinc.notas_novas_nfse, sinc.ult_nsu_nfse))
    s1 = NotaRecebida.objects.get(chave=CHS1)
    t('NFS-e gravada com prestador, número, valor, competência e descrição', s1.tipo_documento == 'NFSE'
      and s1.emitente_cnpj == FORN_B and s1.numero == '123' and s1.valor == D('300.00')
      and s1.competencia == date(2019, 4, 28) and 'câmeras' in s1.descricao and s1.xml_completo.startswith('<NFSe'))
    t('lote menor que 50: espera 1 hora', sinc.proxima_consulta_nfse == agora + timedelta(hours=1))
    adn2 = AdnFalso([lote_adn([(4, 'EVENTO', CHS2, evento_cancelamento_nfse(CHS2))]), lote_adn([], 'NENHUM_DOCUMENTO_LOCALIZADO')])
    sinc = nfse_nacional.sincronizar(NOSSO, transporte=adn2, agora=agora + timedelta(minutes=30))
    t('antes de 1 hora não consulta o ADN', sinc.pulada_nfse and not adn2.pedidos)
    sinc = nfse_nacional.sincronizar(NOSSO, transporte=adn2, agora=agora + timedelta(hours=2))
    t('pede a partir do último NSU', adn2.pedidos[0] == (NOSSO, 3))
    t('evento de cancelamento marca a NFS-e', NotaRecebida.objects.get(chave=CHS2).situacao == 'CANCELADA')
    adn3 = AdnFalso([lote_adn([], 'NENHUM_DOCUMENTO_LOCALIZADO')])
    sinc = nfse_nacional.sincronizar(NOSSO, transporte=adn3, agora=agora + timedelta(hours=4))
    t('nada novo: status guardado e espera', sinc.ultimo_status_nfse == 'NENHUM_DOCUMENTO_LOCALIZADO'
      and sinc.proxima_consulta_nfse == agora + timedelta(hours=5))
    adn4 = AdnFalso([sefaz.SefazErro('ADN respondeu HTTP 500')])
    sinc = nfse_nacional.sincronizar(NOSSO, transporte=adn4, agora=agora + timedelta(hours=6))
    t('erro do ADN: registra e tenta em 15 min', sinc.ultimo_status_nfse == 'ERRO' and '500' in sinc.ultima_mensagem_nfse)

    print('== TELA ==')
    chefe = User.objects.create_user(
        username='zz.sf.chefe', email='zz.sf.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='Zz', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True)
    c = Client()
    c.force_login(chefe)
    receita.buscar = lambda v: {'cnpj': receita.so_digitos(v), 'razao_social': 'ZZ FORNECEDOR B SA', 'nome_fantasia': '',
                                'situacao': 'ATIVA', 'atividade': '', 'simples': False, 'municipio': '', 'uf': 'ES'}
    url = '/contagem-caixa/pis-cofins/?competencia=2019-05&visao=sefaz'
    r = c.get(url)
    html = r.content.decode()
    t('visão SEFAZ abre', r.status_code == 200, r.status_code)
    t('lista as notas do mês', 'ZZ FORNECEDOR A LTDA' in html and 'ZZ FORNECEDOR B SA' in html)
    t('mostra o certificado e a validade curta', 'ZZ EMPRESA TESTE' in html and 'renove' in html)
    t('a lançar: 2 (a cancelada não conta)', '2 a lançar' in html, 'badge')
    t('cancelada aparece riscada', 'Cancelada' in html)
    t('NFS-e na lista pela competência (05/2019), com selo e descrição', 'ZZ MANUTENCAO ME' not in html and 'NFS-e</span>' in html
      and 'Limpeza pós-obra' in html)
    r = c.get('/contagem-caixa/pis-cofins/?competencia=2019-04&visao=sefaz')
    html = r.content.decode()
    t('a NFS-e de competência 04/2019 aparece em abril', 'ZZ MANUTENCAO ME' in html and 'data-competencia="2019-04"' in html
      and 'data-tipo-doc="NFSE"' in html)
    r = c.get('/contagem-caixa/pis-cofins/?competencia=2019-05&visao=sefaz&doc=NFE')
    t('filtro só NF-e esconde as NFS-e', 'Limpeza pós-obra' not in r.content.decode() and 'ZZ FORNECEDOR A LTDA' in r.content.decode())

    settings.SEFAZ_CERTIFICADO_B64 = ''
    r = c.get(url)
    t('sem certificado explica como ligar', 'ainda não está ligada' in r.content.decode() and 'SEFAZ_CERTIFICADO' in r.content.decode())
    r = c.post('/contagem-caixa/pis-cofins/sefaz/buscar/', {'voltar': url}, follow=True)
    t('buscar sem certificado avisa', 'certificado digital A1' in r.content.decode())
    settings.SEFAZ_CERTIFICADO_B64 = base64.b64encode(pfx).decode()

    original = sefaz.sincronizar_todos
    sefaz.sincronizar_todos = lambda transporte=None, prazo_segundos=None: [sefaz.sincronizar(
        NOSSO, transporte=SefazFalsa([resposta('137', 'Nenhum documento localizado', 5, 5)]),
        agora=timezone.now() + timedelta(days=1))]
    r = c.post('/contagem-caixa/pis-cofins/sefaz/buscar/', {'voltar': url}, follow=True)
    t('buscar agora: nenhuma nota nova', 'Nenhuma nota nova na SEFAZ' in r.content.decode())
    sefaz.sincronizar_todos = lambda transporte=None, prazo_segundos=None: [sefaz.sincronizar(NOSSO, transporte=SefazFalsa([]))]
    r = c.post('/contagem-caixa/pis-cofins/sefaz/buscar/', {'voltar': url}, follow=True)
    t('buscar antes da hora: avisa a espera', 'uma consulta por hora' in r.content.decode())
    sefaz.sincronizar_todos = original

    n2 = NotaRecebida.objects.get(chave=CH2)
    r = c.post('/contagem-caixa/pis-cofins/lancar/', {
        'tipo': 'NF', 'competencia': '2019-05', 'cnpj': FORN_B, 'valor': '89,90', 'numero': '4567',
        'data_documento': '2019-05-05', 'nota': n2.id, 'voltar': url})
    n2.refresh_from_db()
    doc = n2.documento
    if doc:
        subidos.append(doc.arquivo.name)
    t('lançar a partir da nota, sem arquivo: o XML vira o arquivo', doc is not None
      and doc.nome_arquivo == f'NFe{CH2}.xml' and doc.arquivo.read().decode().startswith('<resNFe'))
    t('e a nota fica ligada ao documento', doc is not None and doc.valor == D('89.90'))

    # Documento lançado à mão com o número com zeros: liga sozinho na próxima nota/lançamento.
    from django.core.files.uploadedfile import SimpleUploadedFile
    r = c.post('/contagem-caixa/pis-cofins/lancar/', {
        'tipo': 'NF', 'competencia': '2019-05', 'cnpj': FORN_A, 'valor': '1.500,00', 'numero': '000123',
        'arquivo': SimpleUploadedFile('a.pdf', b'%PDF zz'), 'voltar': url})
    n1.refresh_from_db()
    if n1.documento:
        subidos.append(n1.documento.arquivo.name)
    t('lançado à mão (nº 000123) se liga à nota 123', n1.documento is not None and n1.documento.numero == '000123')

    r = c.get(url)
    html = r.content.decode()
    t('sem nada a lançar no mês', 'a lançar</span>' not in html and 'Lançada em 05/2019' in html)
    r = c.get('/contagem-caixa/pis-cofins/?competencia=2019-05')
    t('na visão dos lançados, o selo "na SEFAZ"', 'na SEFAZ' in r.content.decode())

    # Fora do PIS/Cofins.
    ch4 = chave(FORN_B, 9999)
    sefaz.processar_documento(NOSSO, 6, 'resNFe_v1.01.xsd', res_nfe(ch4, FORN_B, 'ZZ FORNECEDOR B SA', '50.00', 9))
    n4 = NotaRecebida.objects.get(chave=ch4)
    r = c.get('/contagem-caixa/pis-cofins/?competencia=2019-05')
    t('lançados avisa a nota que falta', '1 nota</strong> emitida contra o CNPJ em 05/2019' in r.content.decode())
    c.post(f'/contagem-caixa/pis-cofins/sefaz/{n4.id}/fora/', {'voltar': url})
    n4.refresh_from_db()
    t('marca fora do PIS/Cofins, com quem marcou', n4.ignorada and n4.ignorada_por == chefe)
    r = c.get('/contagem-caixa/pis-cofins/?competencia=2019-05')
    t('e o aviso some', 'emitida contra o CNPJ em 05/2019' not in r.content.decode())
    c.post(f'/contagem-caixa/pis-cofins/sefaz/{n4.id}/fora/', {'voltar': url})
    n4.refresh_from_db()
    t('e volta para "a lançar"', not n4.ignorada and n4.ignorada_por is None)
    r = c.get(url + '&nf=a_lancar')
    html = r.content.decode()
    t('filtro "a lançar"', 'nº 9999' in html and 'nº 4567' not in html)
    r = c.get(url + f'&q={FORN_B[:8]}')
    html = r.content.decode()
    t('busca pelo CNPJ do emitente', 'ZZ FORNECEDOR B SA' in html and 'ZZ FORNECEDOR A LTDA' not in html.split('Notas emitidas contra o CNPJ em')[1])

    vend = User.objects.create_user(username='zz.sf.v', email='zz.sf.v@exemplo-teste.local', password='S3nha!teste',
                                    first_name='Zz', last_name='V', hierarchy='SUPERVISOR')
    cv = Client()
    cv.force_login(vend)
    r = cv.post('/contagem-caixa/pis-cofins/sefaz/buscar/')
    t('quem não tem acesso não busca', r.status_code == 302 and r.url == '/')
    r = cv.post(f'/contagem-caixa/pis-cofins/sefaz/{n4.id}/fora/')
    n4.refresh_from_db()
    t('nem marca fora', r.status_code == 302 and not n4.ignorada)
    for k, v in antes.items():
        setattr(settings, k, v)
finally:
    marcador.__exit__(Exception, Exception('rollback'), None)
    storage = DocumentoPisCofins._meta.get_field('arquivo').storage
    for nome in set(subidos):
        try:
            if nome and storage.exists(nome):
                storage.delete(nome)
        except Exception as exc:  # noqa: BLE001
            print('  (não apagou do armazenamento)', nome, exc)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
