"""PIS/Cofins · a nota: ver (DANFE), manifestar, importar pela chave e dar entrada.

Pedido de 16/10/2026: "visualizar a nota fiscal; dar entrada; fazer o
reconhecimento fiscal (ciência, confirmação, desconhecimento, operação não
realizada); importar por chave SEFAZ".

A SEFAZ é falsa e o certificado é autoassinado — e `requests.post/get` ficam
bloqueados: manifestação é ato fiscal real, nada pode sair daqui. O XML que o
portal grava no armazenamento (dar entrada) é apagado no fim.
"""
import base64
import gzip
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-man'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-man-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from lxml import etree

from contagem_caixa import cnpj as receita
from contagem_caixa import manifestacao, sefaz
from contagem_caixa.models import DocumentoPisCofins, ManifestacaoNFe, NotaRecebida
from financeiro.models import LogFinanceiro, Titulo

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def sem_rede(*a, **k):
    raise AssertionError('o teste tentou sair para a rede')


def cnpj_de(base12):
    d = [int(c) for c in base12]
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(d[i] * pesos[i] for i in range(tamanho)) % 11
        d.append(0 if resto < 2 else 11 - resto)
    return ''.join(map(str, d))


def chave(emitente, numero, modelo='55'):
    base = f'321905{emitente}{modelo}001{numero:09d}1{12345678:08d}'
    pesos = [2, 3, 4, 5, 6, 7, 8, 9] * 6
    dv = 11 - sum(int(c) * pesos[i] for i, c in enumerate(reversed(base))) % 11
    return base + str(0 if dv >= 10 else dv)


NOSSO, FORN = cnpj_de('393939390001'), cnpj_de('112223330001')
CH1, CH2 = chave(FORN, 801), chave(FORN, 802)
NS = 'http://www.portalfiscal.inf.br/nfe'


def proc_nfe(ch):
    return (f'<nfeProc xmlns="{NS}" versao="4.00"><NFe><infNFe Id="NFe{ch}" versao="4.00">'
            f'<ide><natOp>VENDA DE MERCADORIA</natOp><mod>55</mod><serie>1</serie><nNF>{int(ch[25:34])}</nNF>'
            f'<dhEmi>2019-05-03T09:00:00-03:00</dhEmi><tpNF>1</tpNF></ide>'
            f'<emit><CNPJ>{FORN}</CNPJ><xNome>ZZ DISTRIBUIDORA LTDA</xNome><xFant>ZZ Dist</xFant><IE>999</IE>'
            f'<enderEmit><xLgr>Rua A</xLgr><nro>10</nro><xBairro>Centro</xBairro><xMun>Vitoria</xMun><UF>ES</UF><CEP>29000000</CEP></enderEmit></emit>'
            f'<dest><CNPJ>{NOSSO}</CNPJ><xNome>ZZ REDE</xNome></dest>'
            f'<det nItem="1"><prod><cProd>A1</cProd><xProd>ZZ CAPA SILICONE</xProd><NCM>39269090</NCM><CFOP>5102</CFOP>'
            f'<uCom>UN</uCom><qCom>10.0000</qCom><vUnCom>15.00</vUnCom><vProd>150.00</vProd></prod>'
            f'<imposto><ICMS><ICMS00><CST>00</CST><vICMS>18.00</vICMS></ICMS00></ICMS></imposto></det>'
            f'<det nItem="2"><prod><cProd>B2</cProd><xProd>ZZ CARREGADOR</xProd><NCM>85044010</NCM><CFOP>5102</CFOP>'
            f'<uCom>UN</uCom><qCom>5</qCom><vUnCom>30.00</vUnCom><vProd>150.00</vProd></prod></det>'
            f'<total><ICMSTot><vBC>150.00</vBC><vICMS>18.00</vICMS><vProd>300.00</vProd><vNF>300.00</vNF></ICMSTot></total>'
            f'<cobr><dup><nDup>001</nDup><dVenc>2019-06-03</dVenc><vDup>150.00</vDup></dup>'
            f'<dup><nDup>002</nDup><dVenc>2019-07-03</dVenc><vDup>150.00</vDup></dup></cobr>'
            f'<pag><detPag><tPag>15</tPag><vPag>300.00</vPag></detPag></pag>'
            f'<infAdic><infCpl>ZZ PEDIDO 555</infCpl></infAdic></infNFe></NFe>'
            f'<protNFe><infProt><nProt>132190000000001</nProt><dhRecbto>2019-05-03T09:01:00-03:00</dhRecbto>'
            f'<xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe></nfeProc>')


def res_nfe(ch):
    return (f'<resNFe xmlns="{NS}" versao="1.01"><chNFe>{ch}</chNFe><CNPJ>{FORN}</CNPJ><xNome>ZZ DISTRIBUIDORA LTDA</xNome>'
            f'<IE>999</IE><dhEmi>2019-05-04T10:00:00-03:00</dhEmi><tpNF>1</tpNF><vNF>80.00</vNF><digVal>x</digVal>'
            f'<dhRecbto>2019-05-04T10:01:00-03:00</dhRecbto><nProt>1</nProt><cSitNFe>1</cSitNFe></resNFe>')


def ret_dist(docs):
    lote = ''.join(f'<docZip NSU="{i:015d}" schema="x">{base64.b64encode(gzip.compress(x.encode())).decode()}</docZip>'
                   for i, x in enumerate(docs, start=1))
    return (f'<?xml version="1.0"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
            f'<retDistDFeInt xmlns="{NS}" versao="1.01"><cStat>138</cStat><xMotivo>Documento localizado</xMotivo>'
            f'<ultNSU>000000000000001</ultNSU><maxNSU>000000000000001</maxNSU><loteDistDFeInt>{lote}</loteDistDFeInt>'
            f'</retDistDFeInt></soap:Body></soap:Envelope>').encode()


def ret_evento(cstat, motivo, protocolo=''):
    return (f'<?xml version="1.0"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
            f'<nfeResultMsg><retEnvEvento xmlns="{NS}" versao="1.00"><idLote>1</idLote><tpAmb>2</tpAmb><cOrgao>91</cOrgao>'
            f'<cStat>128</cStat><xMotivo>Lote de evento processado</xMotivo><retEvento versao="1.00"><infEvento>'
            f'<tpAmb>2</tpAmb><cOrgao>91</cOrgao><cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo>'
            f'<nProt>{protocolo}</nProt></infEvento></retEvento></retEnvEvento></nfeResultMsg></soap:Body></soap:Envelope>').encode()


class SefazEvento:
    def __init__(self, respostas):
        self.respostas, self.pedidos = list(respostas), []

    def __call__(self, corpo):
        self.pedidos.append(corpo)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


# Certificado de teste, com o CNPJ no nome como o e-CNPJ da ICP-Brasil.
chave_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'ZZ EMPRESA TESTE:{NOSSO}')])
cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chave_priv.public_key())
        .serial_number(7).not_valid_before(datetime(2020, 1, 1)).not_valid_after(datetime.now() + timedelta(days=90))
        .sign(chave_priv, hashes.SHA256()))
pfx = pkcs12.serialize_key_and_certificates(b'zz', chave_priv, cert, None, BestAvailableEncryption(b'senha-teste'))
antes = {k: getattr(settings, k, '') for k in ('SEFAZ_CERTIFICADO', 'SEFAZ_CERTIFICADO_B64', 'SEFAZ_CERTIFICADO_SENHA',
                                                 'SEFAZ_CNPJS', 'SEFAZ_AMBIENTE')}
settings.SEFAZ_CERTIFICADO = ''
settings.SEFAZ_CERTIFICADO_B64 = base64.b64encode(pfx).decode()
settings.SEFAZ_CERTIFICADO_SENHA = 'senha-teste'
settings.SEFAZ_CNPJS = f'{NOSSO}:ES'
settings.SEFAZ_AMBIENTE = '2'

subidos = []
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(receita, 'buscar', lambda c: {'cnpj': c, 'razao_social': 'ZZ DISTRIBUIDORA LTDA',
                                                           'nome_fantasia': '', 'situacao': 'ATIVA', 'atividade': '',
                                                           'simples': None, 'municipio': 'VITORIA', 'uf': 'ES'}):
        chefe = User.objects.create_user(username='zzz.manchefe', email='zzz.manchefe@exemplo-teste.local',
                                         password='S3nha!teste', first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN',
                                         is_superuser=True)
        c = Client(HTTP_USER_AGENT='ZZ-Teste', REMOTE_ADDR='10.1.1.1')
        c.force_login(chefe)
        n1 = NotaRecebida.objects.create(chave=CH1, cnpj_destinatario=NOSSO, emitente_cnpj=FORN,
                                         emitente_nome='ZZ DISTRIBUIDORA LTDA', numero='801', serie='1', modelo='55',
                                         valor=Decimal('300.00'), xml_resumo=res_nfe(CH1),
                                         emissao=datetime(2019, 5, 3, 9, tzinfo=datetime.now().astimezone().tzinfo))

        print('== O EVENTO ==')
        ev = manifestacao.montar_evento(CH1, NOSSO, '210210')
        inf = ev.find(f'{{{NS}}}infEvento')
        t('Id = ID + tipo + chave + sequência', inf.get('Id') == f'ID210210{CH1}01')
        t('vai para o Ambiente Nacional (91), homologação', ev.findtext(f'.//{{{NS}}}cOrgao') == '91'
          and ev.findtext(f'.//{{{NS}}}tpAmb') == '2')
        t('descrição oficial sem acento', ev.findtext(f'.//{{{NS}}}descEvento') == 'Ciencia da Operacao')
        dh = ev.findtext(f'.//{{{NS}}}dhEvento')
        t('data com fuso (AAAA-MM-DDTHH:MM:SS-03:00)', len(dh) == 25 and dh[-6] in '+-' and dh[-3] == ':', dh)
        for errado, msg in ((lambda: manifestacao.montar_evento(CH1, NOSSO, '210240', 'curta'), 'não realizada sem justificativa'),
                            (lambda: manifestacao.montar_evento('123', NOSSO, '210210'), 'chave curta'),
                            (lambda: manifestacao.montar_evento(CH1, NOSSO, '999999'), 'tipo desconhecido')):
            try:
                errado()
                t(f'recusa {msg}', False)
            except manifestacao.ManifestacaoInvalida:
                t(f'recusa {msg}', True)
        nr = manifestacao.montar_evento(CH1, NOSSO, '210240', 'Mercadoria devolvida na entrega')
        t('não realizada leva xJust', nr.findtext(f'.//{{{NS}}}xJust') == 'Mercadoria devolvida na entrega')

        print('\n== A ASSINATURA ==')
        assinado = manifestacao.assinar(ev, chave_priv, cert)
        t('assinatura confere (digest e RSA)', manifestacao.verificar_assinatura(assinado))
        mexido = etree.fromstring(etree.tostring(assinado))
        mexido.find(f'.//{{{NS}}}chNFe').text = CH2
        t('mexer no evento depois de assinar quebra a assinatura', not manifestacao.verificar_assinatura(mexido))
        ds = '{http://www.w3.org/2000/09/xmldsig#}'
        ref = assinado.find(f'.//{ds}Reference')
        t('referência aponta para o infEvento', ref.get('URI') == f'#ID210210{CH1}01')
        t('RSA-SHA1 + C14N, como pede o leiaute', assinado.find(f'.//{ds}SignatureMethod').get('Algorithm').endswith('rsa-sha1')
          and assinado.find(f'.//{ds}CanonicalizationMethod').get('Algorithm').endswith('REC-xml-c14n-20010315'))

        print('\n== ENVIAR ==')
        falsa = SefazEvento([ret_evento('135', 'Evento registrado e vinculado a NF-e', '891190000000001')])
        reg = manifestacao.manifestar(n1, '210210', usuario=chefe, transporte=falsa)
        t('ciência registrada', reg.sucesso and reg.cstat == '135' and reg.protocolo == '891190000000001')
        corpo = falsa.pedidos[0]
        t('envelope SOAP do NFeRecepcaoEvento4', 'NFeRecepcaoEvento4' in corpo and '<envEvento' in corpo and '<idLote>' in corpo)
        enviado = etree.fromstring(corpo.encode()).find(f'.//{{{NS}}}evento')
        t('o evento que saiu está assinado e confere', manifestacao.verificar_assinatura(enviado))
        t('guarda o que foi enviado e o que voltou', reg.xml_enviado.startswith('<evento') and '135' in reg.xml_retorno)
        try:
            manifestacao.manifestar(n1, '210210', transporte=SefazEvento([]))
            t('ciência repetida é recusada antes de ir à SEFAZ', False)
        except manifestacao.ManifestacaoInvalida:
            t('ciência repetida é recusada antes de ir à SEFAZ', True)
        reg = manifestacao.manifestar(n1, '210200', usuario=chefe,
                                      transporte=SefazEvento([ret_evento('573', 'Rejeicao: Duplicidade de evento')]))
        t('573 (duplicidade) conta como registrada', reg.sucesso)
        try:
            manifestacao.manifestar(n1, '210210', transporte=SefazEvento([]))
            t('depois de conclusiva, não cabe ciência', False)
        except manifestacao.ManifestacaoInvalida:
            t('depois de conclusiva, não cabe ciência', True)
        n2 = NotaRecebida.objects.create(chave=CH2, cnpj_destinatario=NOSSO, emitente_cnpj=FORN, emitente_nome='ZZ DIST',
                                         numero='802', valor=Decimal('80'), xml_resumo=res_nfe(CH2))
        reg = manifestacao.manifestar(n2, '210220', transporte=SefazEvento([sefaz.SefazErro('SEFAZ respondeu HTTP 503')]))
        t('falha de comunicação fica registrada, sem sucesso', not reg.sucesso and reg.cstat == 'ERRO' and '503' in reg.motivo)
        reg = manifestacao.manifestar(n2, '210220', transporte=SefazEvento([ret_evento('596', 'Rejeicao: Evento apresentado apos o prazo')]))
        t('rejeição da SEFAZ fica registrada', not reg.sucesso and reg.cstat == '596')
        nfse = NotaRecebida.objects.create(chave='9' * 50, tipo_documento='NFSE', cnpj_destinatario=NOSSO,
                                           emitente_cnpj=FORN, numero='5', valor=Decimal('10'))
        try:
            manifestacao.manifestar(nfse, '210210', transporte=SefazEvento([]))
            t('NFS-e não tem manifestação', False)
        except manifestacao.ManifestacaoInvalida:
            t('NFS-e não tem manifestação', True)

        print('\n== PELA TELA ==')
        falsa_tela = SefazEvento([ret_evento('135', 'Evento registrado', '891190000000099')])
        with mock.patch.object(manifestacao, '_transporte_evento', falsa_tela):
            r = c.post(f'/contagem-caixa/pis-cofins/nota/{n2.id}/manifestar/', {'tipo': '210240', 'justificativa': 'curta'})
            t('justificativa curta é recusada', not n2.manifestacoes.filter(tipo='210240').exists() and not falsa_tela.pedidos)
            r = c.post(f'/contagem-caixa/pis-cofins/nota/{n2.id}/manifestar/', {'tipo': '210220'})
        t('desconhecimento enviado pela tela', n2.manifestacoes.filter(tipo='210220', sucesso=True).exists())
        log = LogFinanceiro.objects.filter(acao='MANIFESTAR', usuario=chefe).first()
        t('e registrado na rastreabilidade com IP', log is not None and log.ip == '10.1.1.1' and '891190000000099' in str(log.depois))

        print('\n== IMPORTAR PELA CHAVE ==')
        falsa_dist = SefazEvento([ret_dist([proc_nfe(CH1)])])
        nota, msg = manifestacao.importar_por_chave(CH1, NOSSO, transporte=falsa_dist)
        t('pede pela chave (consChNFe)', f'<consChNFe><chNFe>{CH1}</chNFe></consChNFe>' in falsa_dist.pedidos[0])
        t('o XML completo chega na nota que já existia', nota.pk == n1.pk and nota.xml_completo.startswith('<nfeProc'), msg)
        CH3 = chave(FORN, 803)
        with mock.patch.object(sefaz, '_transporte_https', SefazEvento([ret_dist([res_nfe(CH3)])])):
            r = c.post('/contagem-caixa/pis-cofins/sefaz/chave/', {'chave': CH3, 'cnpj': NOSSO})
        n3 = NotaRecebida.objects.filter(chave=CH3).first()
        t('pela tela, nota nova entra e abre a página dela', n3 is not None and r.status_code == 302
          and r.url == f'/contagem-caixa/pis-cofins/nota/{n3.id}/', getattr(r, 'url', r.status_code))
        r = c.post('/contagem-caixa/pis-cofins/sefaz/chave/', {'chave': '123'})
        t('chave errada avisa', r.status_code == 302 and 'visao=sefaz' in r.url)
        try:
            manifestacao.importar_por_chave(CH3, '11111111000111', transporte=SefazEvento([]))
            t('CNPJ de fora da empresa é recusado', False)
        except manifestacao.ManifestacaoInvalida:
            t('CNPJ de fora da empresa é recusado', True)

        print('\n== VER A NOTA ==')
        html = c.get(f'/contagem-caixa/pis-cofins/nota/{n1.id}/').content.decode()
        t('DANFE com emitente e natureza', 'ZZ DISTRIBUIDORA LTDA' in html and 'VENDA DE MERCADORIA' in html)
        t('com os itens', 'ZZ CAPA SILICONE' in html and 'ZZ CARREGADOR' in html)
        t('duplicatas e pagamento', '03/06/2019' in html and 'Boleto bancário' in html)
        t('protocolo e informações complementares', '132190000000001' in html and 'ZZ PEDIDO 555' in html)
        t('histórico das manifestações', 'Ciência da operação' in html and '891190000000001' in html)
        html = c.get(f'/contagem-caixa/pis-cofins/nota/{n3.id}/').content.decode()
        t('só com resumo, explica a ciência', 'Só o resumo da SEFAZ' in html and 'Ciência da operação' in html)
        r = c.get(f'/contagem-caixa/pis-cofins/nota/{n1.id}/xml/')
        t('baixa o XML', r.status_code == 200 and f'NFe{CH1}.xml' in r['Content-Disposition'])
        html = c.get('/contagem-caixa/pis-cofins/?visao=sefaz&competencia=2019-05').content.decode()
        t('a lista das notas leva à nota', f'/contagem-caixa/pis-cofins/nota/{n1.id}/' in html)
        t('com o selo da manifestação', 'Confirmação da operação' in html)
        t('e o campo de importar pela chave', '/contagem-caixa/pis-cofins/sefaz/chave/' in html)

        print('\n== DAR ENTRADA ==')
        r = c.post(f'/contagem-caixa/pis-cofins/nota/{n1.id}/entrada/', {
            'competencia': '2019-05', 'descricao': 'ZZ compra de acessórios', 'gerar_titulos': '1',
            'parcela_vencimento': ['2019-06-03', '2019-07-03'], 'parcela_valor': ['150.00', '150.00'], 'forma': 'BOLETO'})
        n1.refresh_from_db()
        doc = n1.documento
        if doc and doc.arquivo:
            subidos.append(doc.arquivo.name)
        t('lançada no PIS/Cofins', doc is not None and doc.competencia.isoformat() == '2019-05-01'
          and doc.valor == Decimal('300.00') and doc.fornecedor.cnpj == FORN)
        t('com o XML anexado', doc is not None and doc.arquivo.name.endswith('.xml'))
        tits = list(Titulo.objects.filter(nota=n1).order_by('vencimento'))
        t('duas contas a pagar no Financeiro', len(tits) == 2 and [x.valor for x in tits] == [Decimal('150.00')] * 2)
        t('com origem na nota e fornecedor', all(x.origem == 'NFE' and x.pessoa_documento == FORN and x.tipo == 'PAGAR' for x in tits))
        r = c.post(f'/contagem-caixa/pis-cofins/nota/{n1.id}/entrada/', {
            'competencia': '2019-05', 'gerar_titulos': '1', 'parcela_vencimento': ['2019-06-03'], 'parcela_valor': ['300']})
        t('não gera contas a pagar duas vezes', Titulo.objects.filter(nota=n1).count() == 2)
        t('a entrada fica na rastreabilidade', LogFinanceiro.objects.filter(descricao__startswith='Deu entrada na NF 801').exists())

        print('\n== QUEM PODE ==')
        sem = User.objects.create_user(username='zzz.mansem', email='zzz.mansem@exemplo-teste.local',
                                       password='S3nha!teste', first_name='ZZ', hierarchy='SUPERVISOR')
        cs = Client(); cs.force_login(sem)
        t('sem acesso ao PIS/Cofins não vê a nota', cs.get(f'/contagem-caixa/pis-cofins/nota/{n1.id}/').status_code == 302)
        antes_n = ManifestacaoNFe.objects.count()
        cs.post(f'/contagem-caixa/pis-cofins/nota/{n3.id}/manifestar/', {'tipo': '210210'})
        t('nem manifesta', ManifestacaoNFe.objects.count() == antes_n)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    for k, v in antes.items():
        setattr(settings, k, v)
    storage = DocumentoPisCofins._meta.get_field('arquivo').storage
    for nome_arq in set(subidos):
        try:
            if nome_arq and storage.exists(nome_arq):
                storage.delete(nome_arq)
                print('  (apagado do armazenamento)', nome_arq)
        except Exception as exc:  # noqa: BLE001
            print('  (não apagou do armazenamento)', nome_arq, exc)
    print('\nrollback: nada gravado no banco; nada enviado à SEFAZ.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
