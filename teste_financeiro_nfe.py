"""Financeiro · emissão de NF-e (saída, entrada e devolução) direto na SEFAZ.

Pedido de 26/11/2026: "Emissão de Nota Fiscal, as vezes precisa de nota fiscal
de devolução, e precisa das nota de entrada, basicamente entrada e saida de
nota, tente usar api da SEFAZ ou algo do tipo, deve ser dentro do portal".

A SEFAZ é falsa (confere a assinatura de cada XML que chega e responde como a
de verdade), o certificado A1 é autoassinado e `requests.post/get` ficam
bloqueados: emitir NF-e é ato fiscal real, nada sai daqui. Tudo roda numa
transação desfeita no fim; o cache é LocMem.
"""
import base64
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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-nfe'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-nfe-2'},
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
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from lxml import etree

from contagem_caixa.models import NotaRecebida
from financeiro import danfe, nfe
from financeiro.models import AcessoFinanceiro, LogFinanceiro
from financeiro.models_nfe import (ConfiguracaoNFe, EmitenteNFe, EventoNotaFiscal, ItemNotaFiscal,
                                   NotaFiscalEmitida)
from users.models import Sector

User = get_user_model()
ok = fail = 0
NS = nfe.NS


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


def chave_independente(base43):
    """DV da chave calculado do jeito do manual (pesos 2..9 da direita), sem usar o código do portal."""
    pesos = [2, 3, 4, 5, 6, 7, 8, 9] * 6
    dv = 11 - sum(int(c) * pesos[i] for i, c in enumerate(reversed(base43))) % 11
    return base43 + str(0 if dv >= 10 else dv)


MATRIZ, FILIAL_RJ, FORN, CLIENTE = (cnpj_de('393939390001'), cnpj_de('393939390002'), cnpj_de('112223330001'),
                                    cnpj_de('445556660001'))


def certificado(cnpj, dias=90, senha=b'senha-teste'):
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'ZZ EMPRESA TESTE:{cnpj}')])
    inicio = datetime(2020, 1, 1)
    fim = datetime.now() + timedelta(days=dias)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chave.public_key())
            .serial_number(11).not_valid_before(inicio).not_valid_after(fim).sign(chave, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b'zz', chave, cert, None, BestAvailableEncryption(senha))


class SefazFalsa:
    """Responde como a SEFAZ: autoriza, rejeita, recebe em lote ou cai — e confere a assinatura."""

    def __init__(self):
        self.modo = 'autoriza'
        self.chamadas = []           # (servico, uf, ambiente)
        self.assinaturas = []        # True/False de cada XML assinado que chegou
        self.autorizadas = {}        # chave → protocolo
        self.ultimo = {}
        self.n = 0

    def _soap(self, corpo):
        return ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
                f'<soap:Body><nfeResultMsg>{corpo}</nfeResultMsg></soap:Body></soap:Envelope>').encode()

    def _prot(self, chave, cstat='100', motivo='Autorizado o uso da NF-e'):
        self.n += 1
        protocolo = f'13226{self.n:010d}'
        if cstat == '100':
            self.autorizadas[chave] = protocolo
        return (f'<protNFe versao="4.00"><infProt><tpAmb>2</tpAmb><verAplic>ZZ</verAplic><chNFe>{chave}</chNFe>'
                f'<dhRecbto>2026-11-26T10:00:00-03:00</dhRecbto>{"<nProt>" + protocolo + "</nProt>" if cstat == "100" else ""}'
                f'<digVal>x</digVal><cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo></infProt></protNFe>')

    def __call__(self, servico, uf, ambiente, corpo, config):
        self.chamadas.append((servico, uf, ambiente))
        raiz = etree.fromstring(corpo.encode('utf-8'))
        self.ultimo[servico] = raiz
        if servico == 'status':
            return self._soap(f'<retConsStatServ xmlns="{NS}" versao="4.00"><tpAmb>{ambiente}</tpAmb><cStat>107</cStat>'
                              '<xMotivo>Servico em Operacao</xMotivo><tMed>1</tMed></retConsStatServ>')
        if servico == 'autorizacao':
            el = raiz.find(f'.//{{{NS}}}NFe')
            self.assinaturas.append(nfe.verificar_assinatura(el, 'infNFe'))
            chave = el.find(f'{{{NS}}}infNFe').get('Id')[3:]
            if self.modo == 'cai':
                raise nfe.SefazErro('A SEFAZ não respondeu: tempo esgotado')
            if self.modo == 'lote':
                self._pendente = chave
                return self._soap(f'<retEnviNFe xmlns="{NS}" versao="4.00"><tpAmb>2</tpAmb><cStat>103</cStat>'
                                  '<xMotivo>Lote recebido com sucesso</xMotivo><infRec><nRec>321000000000099</nRec>'
                                  '<tMed>1</tMed></infRec></retEnviNFe>')
            prot = (self._prot(chave, '778', 'Rejeicao: Informado NCM inexistente') if self.modo == 'rejeita'
                    else self._prot(chave))
            return self._soap(f'<retEnviNFe xmlns="{NS}" versao="4.00"><tpAmb>2</tpAmb><cStat>104</cStat>'
                              f'<xMotivo>Lote processado</xMotivo>{prot}</retEnviNFe>')
        if servico == 'retorno':
            return self._soap(f'<retConsReciNFe xmlns="{NS}" versao="4.00"><tpAmb>2</tpAmb><cStat>104</cStat>'
                              f'<xMotivo>Lote processado</xMotivo>{self._prot(self._pendente)}</retConsReciNFe>')
        if servico == 'consulta':
            chave = raiz.find(f'.//{{{NS}}}chNFe').text
            if chave in self.autorizadas or self.modo == 'consulta_autoriza':
                prot = self._prot(chave) if chave not in self.autorizadas else (
                    f'<protNFe versao="4.00"><infProt><tpAmb>2</tpAmb><chNFe>{chave}</chNFe><nProt>{self.autorizadas[chave]}</nProt>'
                    '<cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe>')
                return self._soap(f'<retConsSitNFe xmlns="{NS}" versao="4.00"><tpAmb>2</tpAmb><cStat>100</cStat>'
                                  f'<xMotivo>Autorizado o uso da NF-e</xMotivo><chNFe>{chave}</chNFe>{prot}</retConsSitNFe>')
            return self._soap(f'<retConsSitNFe xmlns="{NS}" versao="4.00"><tpAmb>2</tpAmb><cStat>217</cStat>'
                              f'<xMotivo>Rejeicao: NF-e nao consta na base de dados da SEFAZ</xMotivo></retConsSitNFe>')
        if servico == 'evento':
            el = raiz.find(f'.//{{{NS}}}evento')
            self.assinaturas.append(nfe.verificar_assinatura(el, 'infEvento'))
            tipo = el.find(f'.//{{{NS}}}tpEvento').text
            self.n += 1
            cstat = '135'
            return self._soap(f'<retEnvEvento xmlns="{NS}" versao="1.00"><idLote>1</idLote><tpAmb>2</tpAmb>'
                              '<cOrgao>32</cOrgao><cStat>128</cStat><xMotivo>Lote de evento processado</xMotivo>'
                              f'<retEvento versao="1.00"><infEvento><tpAmb>2</tpAmb><cOrgao>32</cOrgao><cStat>{cstat}</cStat>'
                              f'<xMotivo>Evento registrado e vinculado a NF-e</xMotivo><tpEvento>{tipo}</tpEvento>'
                              f'<nProt>13226{self.n:010d}</nProt></infEvento></retEvento></retEnvEvento>')
        raise AssertionError(f'serviço inesperado: {servico}')


def proc_nfe_fornecedor(ch, cfop='5102'):
    """NF-e de um fornecedor do ES para a matriz (a que vamos devolver)."""
    return (f'<nfeProc xmlns="{NS}" versao="4.00"><NFe><infNFe Id="NFe{ch}" versao="4.00">'
            f'<ide><natOp>VENDA</natOp><mod>55</mod><serie>1</serie><nNF>{int(ch[25:34])}</nNF>'
            f'<dhEmi>2019-05-03T09:00:00-03:00</dhEmi><tpNF>1</tpNF><finNFe>1</finNFe></ide>'
            f'<emit><CNPJ>{FORN}</CNPJ><xNome>ZZ DISTRIBUIDORA LTDA</xNome><IE>082000001</IE>'
            f'<enderEmit><xLgr>Rua A</xLgr><nro>10</nro><xBairro>Centro</xBairro><cMun>3205309</cMun><xMun>Vitoria</xMun>'
            f'<UF>ES</UF><CEP>29000000</CEP><fone>2733330000</fone></enderEmit><CRT>3</CRT></emit>'
            f'<dest><CNPJ>{MATRIZ}</CNPJ><xNome>ZZ REDE</xNome><enderDest><xLgr>Av B</xLgr><nro>1</nro><xBairro>Praia</xBairro>'
            f'<cMun>3205309</cMun><xMun>Vitoria</xMun><UF>ES</UF></enderDest><indIEDest>1</indIEDest><IE>083000002</IE></dest>'
            f'<det nItem="1"><prod><cProd>A1</cProd><cEAN>SEM GTIN</cEAN><xProd>ZZ CAPA SILICONE</xProd><NCM>39269090</NCM>'
            f'<CFOP>{cfop}</CFOP><uCom>UN</uCom><qCom>10.0000</qCom><vUnCom>15.00</vUnCom><vProd>150.00</vProd></prod>'
            f'<imposto><ICMS><ICMS00><orig>0</orig><CST>00</CST><modBC>3</modBC><vBC>150.00</vBC><pICMS>17.0000</pICMS>'
            f'<vICMS>25.50</vICMS></ICMS00></ICMS></imposto></det>'
            f'<total><ICMSTot><vBC>150.00</vBC><vICMS>25.50</vICMS><vProd>150.00</vProd><vNF>150.00</vNF></ICMSTot></total>'
            f'</infNFe></NFe><protNFe><infProt><nProt>132190000000001</nProt></infProt></protNFe></nfeProc>')


def nova_nota(emitente, **kw):
    dados = dict(emitente=emitente, loja=emitente.loja, tipo_operacao='1', finalidade='1', natureza='Venda de mercadoria',
                 dest_documento=CLIENTE, dest_nome='ZZ CLIENTE COMPRADOR LTDA', dest_ie='081234567', dest_ind_ie='1',
                 dest_logradouro='Rua do Cliente', dest_numero='50', dest_bairro='Centro', dest_codigo_municipio='3205200',
                 dest_municipio='Vila Velha', dest_uf='ES', dest_cep='29100000', presenca='1', ambiente='2')
    dados.update(kw)
    itens = dados.pop('itens', None)
    nota = NotaFiscalEmitida.objects.create(**dados)
    for i, item in enumerate(itens or [dict(codigo='A1', descricao='ZZ CAPA SILICONE', ncm='39269090', cfop='5102',
                                            quantidade=Decimal('10'), valor_unitario=Decimal('15'), cst_icms='00',
                                            aliquota_icms=Decimal('17'))], start=1):
        ItemNotaFiscal.objects.create(nota=nota, ordem=i, **item)
    return nota


sefaz = SefazFalsa()
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(nfe, '_transporte_https', sefaz):

        print('\n== CHAVE, CÁLCULO E CONFERÊNCIA ==')
        base = f'3226110{MATRIZ}5500100000004211234567'[:43]
        t('DV da chave igual ao do manual', nfe.dv_modulo11(base) == chave_independente(base)[-1])
        for amostra in ('35190612345678000190550010000001231000000010', '32261109163602000134550010000000011123456780'):
            base43 = amostra[:43]
            t(f'DV confere em {base43[:8]}…', nfe.dv_modulo11(base43) == chave_independente(base43)[-1])
        t('chave com dígito errado é recusada', not nfe.chave_valida(base + str((int(nfe.dv_modulo11(base)) + 1) % 10)))

        loja_es = Sector.objects.create(name='Loja ZZ NFe Vitoria')
        loja_rj = Sector.objects.create(name='Loja ZZ NFe Rio')
        emit = EmitenteNFe.objects.create(loja=loja_es, cnpj=MATRIZ, razao_social='ZZ REDE TELECOM LTDA', ie='083000002',
                                          crt='3', logradouro='Av B', numero='1', bairro='Praia',
                                          codigo_municipio='3205309', municipio='Vitoria', uf='ES', cep='29000000')
        emit_rj = EmitenteNFe.objects.create(loja=loja_rj, cnpj=FILIAL_RJ, razao_social='ZZ REDE TELECOM LTDA',
                                             ie='77123456', crt='1', logradouro='Rua C', numero='2', bairro='Centro',
                                             codigo_municipio='3304557', municipio='Rio de Janeiro', uf='RJ',
                                             cep='20000000')

        n = nova_nota(emit, valor_frete=Decimal('10.00'), itens=[
            dict(codigo='A1', descricao='ZZ CAPA', ncm='39269090', cfop='5102', quantidade=Decimal('10'),
                 valor_unitario=Decimal('15'), cst_icms='00', aliquota_icms=Decimal('17'), cst_pis='01',
                 aliquota_pis=Decimal('1.65'), cst_cofins='01', aliquota_cofins=Decimal('7.6')),
            dict(codigo='B2', descricao='ZZ CARREGADOR', ncm='85044010', cfop='5102', quantidade=Decimal('5'),
                 valor_unitario=Decimal('30'), valor_desconto=Decimal('10'), cst_icms='40')])
        itens = list(n.itens.all())
        nfe.calcular(n, itens)
        t('frete rateado pelos itens (5 + 5)', nfe.rateios(n, itens)[0] == [Decimal('5.00'), Decimal('5.00')])
        t('BC do ICMS = produto + frete do item', itens[0].base_icms == Decimal('155.00'), itens[0].base_icms)
        t('ICMS 17% de 155,00 = 26,35', itens[0].valor_icms == Decimal('26.35'), itens[0].valor_icms)
        t('CST 40 não tem ICMS', itens[1].valor_icms == 0 and itens[1].base_icms == 0)
        t('PIS 1,65% e Cofins 7,6% sobre 150', (itens[0].valor_pis, itens[0].valor_cofins) == (Decimal('2.48'), Decimal('11.40')))
        t('total = 300 − 10 de desconto + 10 de frete', n.valor_total == Decimal('300.00'), n.valor_total)
        t('nota em ordem não tem problema', nfe.validar(n, itens) == [], nfe.validar(n, itens))

        n.dest_uf = 'RJ'
        t('CFOP 5102 para outro estado é apontado', any('CFOP 5102' in e for e in nfe.validar(n, itens)))
        n.dest_uf = 'ES'
        n.finalidade = '4'
        erros = nfe.validar(n, itens)
        t('devolução sem chave é apontada', any('chave' in e for e in erros))
        t('devolução com CFOP de venda é apontada', any('CFOP de devolução' in e for e in erros))
        n.finalidade = '1'
        itens[1].cst_icms = '102'
        t('CSOSN no regime normal é apontado', any("'102'" in e for e in nfe.validar(n, itens)))
        itens[1].cst_icms = '40'
        n.tipo_operacao = '0'
        t('CFOP de saída numa entrada é apontado', any('começa com 1' in e for e in nfe.validar(n, itens)))
        n.tipo_operacao = '1'
        n.dest_ind_ie, n.consumidor_final = '9', False
        nfe.calcular(n, itens)
        t('não contribuinte vira consumidor final', n.consumidor_final is True)
        n.dest_ind_ie = '1'

        print('\n== CERTIFICADO GUARDADO CIFRADO ==')
        nova_config = ConfiguracaoNFe()
        t('nasce desligada e em homologação', (nova_config.ligado, nova_config.ambiente) == (False, '2'))
        # O banco de dev pode já estar configurado de verdade: o teste parte do zero (desfeito no fim).
        ConfiguracaoNFe.objects.filter(pk=1).delete()
        config = ConfiguracaoNFe.atual()
        t('configuração do zero: sem certificado', not config.tem_certificado and not config.ligado)
        pfx = certificado(MATRIZ)
        try:
            nfe.guardar_certificado(config, pfx, 'senha-errada')
            t('senha errada é recusada', False)
        except nfe.NfeInvalida:
            t('senha errada é recusada', True)
        try:
            nfe.guardar_certificado(config, certificado(MATRIZ, dias=-3), 'senha-teste')
            t('certificado vencido é recusado', False)
        except nfe.NfeInvalida as exc:
            t('certificado vencido é recusado', 'venceu' in str(exc), exc)
        nfe.guardar_certificado(config, pfx, 'senha-teste')
        config.refresh_from_db()
        t('titular e CNPJ lidos do certificado', config.certificado_cnpj == MATRIZ and 'ZZ EMPRESA' in config.certificado_titular)
        t('o banco não guarda o .pfx nem a senha às claras',
          base64.b64encode(pfx).decode()[:40] not in config.certificado_cifrado and 'senha-teste' not in config.senha_cifrada)
        t('o certificado guardado abre de novo', nfe.carregar_certificado(config)[1] is not None)

        print('\n== DESLIGADA: NADA SAI ==')
        try:
            nfe.enviar(n)
            t('enviar com a emissão desligada é recusado', False)
        except nfe.NfeInvalida as exc:
            t('enviar com a emissão desligada é recusado', 'desligada' in str(exc))
        n.refresh_from_db()
        emit.refresh_from_db()
        t('e não gasta número', n.numero is None and emit.proximo_numero_homologacao == 1)
        t('nenhuma ida à SEFAZ até aqui', sefaz.chamadas == [])

        print('\n== SAÍDA AUTORIZADA (homologação) ==')
        config.ligado = True
        config.resp_tec_cnpj, config.resp_tec_contato, config.resp_tec_email = MATRIZ, 'TI ZZ', 'ti@zz.local'
        config.save()
        n = nfe.enviar(n)
        emit.refresh_from_db()
        t('autorizada', n.status == 'AUTORIZADA', f'{n.status} {n.cstat} {n.motivo}')
        t('número 1 da série 1', (n.numero, n.serie) == (1, 1))
        t('homologação numera separado da produção', emit.proximo_numero_homologacao == 2 and emit.proximo_numero == 1)
        t('chave válida: ES, CNPJ do emitente, modelo 55, número 1',
          nfe.chave_valida(n.chave) and n.chave[:2] == '32' and n.chave[6:20] == MATRIZ and n.chave[20:22] == '55'
          and int(n.chave[25:34]) == 1)
        t('chave confere com o cálculo do manual', n.chave == chave_independente(n.chave[:43]))
        t('a SEFAZ recebeu XML com assinatura válida', sefaz.assinaturas == [True])
        t('foi à SEFAZ do ES (SVRS) em homologação', sefaz.chamadas[-1] == ('autorizacao', 'ES', '2'))
        proc = etree.fromstring(n.xml_autorizado.encode())
        t('XML autorizado é nfeProc com protNFe', proc.tag == f'{{{NS}}}nfeProc' and proc.find(f'{{{NS}}}protNFe') is not None)
        t('protocolo gravado', n.protocolo and n.protocolo == proc.find(f'.//{{{NS}}}nProt').text)
        ide = proc.find(f'.//{{{NS}}}ide')
        t('tpAmb 2, tpNF 1, finNFe 1, idDest 1',
          [ide.find(f'{{{NS}}}{x}').text for x in ('tpAmb', 'tpNF', 'finNFe', 'idDest')] == ['2', '1', '1', '1'])
        t('homologação troca o nome do destinatário (regra da SEFAZ)',
          proc.find(f'.//{{{NS}}}dest/{{{NS}}}xNome').text == nfe.XNOME_HOMOLOGACAO)
        t('assinatura do XML guardado confere', nfe.verificar_assinatura(proc.find(f'{{{NS}}}NFe'), 'infNFe'))
        tot = proc.find(f'.//{{{NS}}}ICMSTot')
        t('totais no XML', (tot.find(f'{{{NS}}}vNF').text, tot.find(f'{{{NS}}}vICMS').text, tot.find(f'{{{NS}}}vFrete').text)
          == ('300.00', '26.35', '10.00'))
        ordem_ide = [el.tag.split('}')[1] for el in ide]
        t('ordem do ide segue o leiaute', ordem_ide[:8] == ['cUF', 'cNF', 'natOp', 'mod', 'serie', 'nNF', 'dhEmi', 'dhSaiEnt']
          and ordem_ide.index('procEmi') > ordem_ide.index('indPres'))
        inf = proc.find(f'.//{{{NS}}}infNFe')
        t('grupos do infNFe na ordem do leiaute', [el.tag.split('}')[1] for el in inf] ==
          ['ide', 'emit', 'dest', 'det', 'det', 'total', 'transp', 'pag', 'infRespTec'])
        det2 = inf.findall(f'{{{NS}}}det')[1]
        t('CST 40 sai no grupo ICMS40', det2.find(f'.//{{{NS}}}ICMS40') is not None)
        t('PIS 01 sai em PISAliq', inf.find(f'.//{{{NS}}}PISAliq/{{{NS}}}vPIS').text == '2.48')
        t('cUF e cNF batem com a chave', ide.find(f'{{{NS}}}cNF').text == n.chave[35:43] and ide.find(f'{{{NS}}}cDV').text == n.chave[-1])
        pdf = danfe.gerar(n.xml_autorizado)
        t('DANFE em PDF', pdf[:5] == b'%PDF-' and len(pdf) > 3000)

        print('\n== REJEIÇÃO: CORRIGE E REENVIA COM O MESMO NÚMERO ==')
        n2 = nova_nota(emit)
        sefaz.modo = 'rejeita'
        n2 = nfe.enviar(n2)
        t('rejeitada com o motivo da SEFAZ', n2.status == 'REJEITADA' and n2.cstat == '778' and 'NCM' in n2.motivo)
        numero_reservado = n2.numero
        t('o número fica com a nota', numero_reservado == 2)
        sefaz.modo = 'autoriza'
        n2 = nfe.enviar(n2)
        t('reenviada e autorizada com o mesmo número', n2.status == 'AUTORIZADA' and n2.numero == numero_reservado)
        emit.refresh_from_db()
        t('sem buraco na numeração', emit.proximo_numero_homologacao == 3)
        try:
            nfe.enviar(n2)
            t('autorizada não se envia de novo', False)
        except nfe.NfeInvalida:
            t('autorizada não se envia de novo', True)

        print('\n== LOTE EM PROCESSAMENTO E SEFAZ FORA DO AR ==')
        n3 = nova_nota(emit)
        sefaz.modo = 'lote'
        n3 = nfe.enviar(n3)
        t('lote recebido: fica em processamento com o recibo', n3.status == 'PROCESSANDO' and n3.recibo == '321000000000099')
        n3 = nfe.consultar(n3)
        t('consulta do recibo autoriza', n3.status == 'AUTORIZADA' and n3.recibo == '' and sefaz.chamadas[-1][0] == 'retorno')
        n4 = nova_nota(emit)
        sefaz.modo = 'cai'
        n4 = nfe.enviar(n4)
        t('SEFAZ fora do ar: fica em processamento (pode ter chegado)', n4.status == 'PROCESSANDO' and n4.cstat == 'ERRO')
        sefaz.modo = 'consulta_autoriza'
        n4 = nfe.consultar(n4)
        t('consulta pela chave resolve', n4.status == 'AUTORIZADA' and sefaz.chamadas[-1][0] == 'consulta')
        sefaz.modo = 'autoriza'

        print('\n== CARTA DE CORREÇÃO E CANCELAMENTO ==')
        try:
            nfe.corrigir(n, 'curta')
            t('correção curta é recusada', False)
        except nfe.NfeInvalida:
            t('correção curta é recusada', True)
        cce = nfe.corrigir(n, 'Correção do endereço de entrega: Rua do Cliente, 50, fundos')
        t('carta de correção registrada', cce.sucesso and cce.sequencia == 1 and cce.protocolo)
        ev = etree.fromstring(cce.xml_enviado.encode())
        t('evento 110110 com a condição de uso oficial',
          ev.find(f'.//{{{NS}}}tpEvento').text == '110110' and ev.find(f'.//{{{NS}}}xCondUso').text == nfe.CONDICAO_CCE)
        t('correção vai sem acento', 'Correcao do endereco' in ev.find(f'.//{{{NS}}}xCorrecao').text)
        t('evento vai para o órgão do emitente (ES = 32)', ev.find(f'.//{{{NS}}}cOrgao').text == '32')
        cce2 = nfe.corrigir(n, 'Segunda correção: complemento do endereço é fundos')
        t('a segunda carta é a sequência 2', cce2.sequencia == 2 and etree.fromstring(cce2.xml_enviado.encode())
          .find(f'.//{{{NS}}}nSeqEvento').text == '2')
        try:
            nfe.cancelar(n, 'erro')
            t('cancelamento sem justificativa de 15 caracteres é recusado', False)
        except nfe.NfeInvalida:
            t('cancelamento sem justificativa de 15 caracteres é recusado', True)
        canc = nfe.cancelar(n, 'Venda desfeita pelo cliente antes da entrega')
        n.refresh_from_db()
        t('cancelada', canc.sucesso and n.status == 'CANCELADA' and n.cancelada_em)
        evc = etree.fromstring(canc.xml_enviado.encode())
        t('cancelamento leva o protocolo da autorização', evc.find(f'.//{{{NS}}}nProt').text == n.protocolo)
        t('procEventoNFe guardado', '<procEventoNFe' in canc.xml_retorno)
        t('todos os eventos chegaram assinados certo', all(sefaz.assinaturas), sefaz.assinaturas)
        try:
            nfe.corrigir(n, 'Correção depois de cancelada não pode')
            t('nota cancelada não aceita correção', False)
        except nfe.NfeInvalida:
            t('nota cancelada não aceita correção', True)

        print('\n== DEVOLUÇÃO A PARTIR DA NOTA ORIGINAL ==')
        base_ch = f'321905{FORN}55001000000801' + '1' + '12345678'
        ch_forn = chave_independente(base_ch)
        NotaRecebida.objects.create(chave=ch_forn, cnpj_destinatario=MATRIZ, emitente_cnpj=FORN, emitente_nome='ZZ DIST',
                                    modelo='55', serie='1', numero='801', valor=Decimal('150'),
                                    xml_completo=proc_nfe_fornecedor(ch_forn))
        dados = nfe.dados_para_devolucao(ch_forn, [emit, emit_rj])
        d = dados['nota']
        t('devolver compra = nota de SAÍDA, finalidade 4', (d['tipo_operacao'], d['finalidade']) == ('1', '4'))
        t('destinatário = o fornecedor', d['dest_documento'] == FORN and d['dest_codigo_municipio'] == '3205309')
        t('emitente = quem recebeu a nota', d['emitente'] == emit.pk and d['chave_referenciada'] == ch_forn)
        t('CFOP 5102 vira 5202 no mesmo estado', dados['itens'][0]['cfop'] == '5202')
        t('quantidade, valor e ICMS da nota original', (dados['itens'][0]['quantidade'], dados['itens'][0]['valor_unitario'],
                                                       dados['itens'][0]['cst_icms'], dados['itens'][0]['aliquota_icms'])
          == ('10.0000', '15.00', '00', '17.0000'))
        t('CFOP de ST (5405) vira 5411', nfe.cfop_de_devolucao('5405', True, True) == '5411')
        t('devolução de venda para outro estado: 2202', nfe.cfop_de_devolucao('6102', False, False) == '2202')
        dev = nova_nota(emit, **{k: v for k, v in d.items() if k not in ('emitente', 'original')}, itens=[
            {**{k: (Decimal(v) if k in ('quantidade', 'valor_unitario', 'valor_desconto', 'aliquota_icms', 'reducao_bc_icms',
                                        'aliquota_pis', 'aliquota_cofins') else v) for k, v in dados['itens'][0].items()},
             'quantidade': Decimal('4')}])
        t('rascunho de devolução parcial está em ordem', nfe.validar(dev, list(dev.itens.all())) == [],
          nfe.validar(dev, list(dev.itens.all())))
        dev = nfe.enviar(dev)
        t('devolução autorizada', dev.status == 'AUTORIZADA', f'{dev.cstat} {dev.motivo}')
        xd = etree.fromstring(dev.xml_autorizado.encode())
        t('devolução referencia a chave original (NFref/refNFe)', xd.find(f'.//{{{NS}}}NFref/{{{NS}}}refNFe').text == ch_forn)
        t('devolução sai com finNFe 4 e sem pagamento (tPag 90)',
          xd.find(f'.//{{{NS}}}finNFe').text == '4' and xd.find(f'.//{{{NS}}}tPag').text == '90'
          and xd.find(f'.//{{{NS}}}vPag').text == '0.00')
        t('informação complementar cita a nota devolvida', 'numero 801' in xd.find(f'.//{{{NS}}}infCpl').text)
        dados_venda = nfe.dados_para_devolucao(n2.chave, [emit])
        t('devolução da nossa venda = nota de ENTRADA com CFOP 1202',
          dados_venda['nota']['tipo_operacao'] == '0' and dados_venda['itens'][0]['cfop'] == '1202'
          and dados_venda['nota']['dest_documento'] == CLIENTE)
        try:
            nfe.dados_para_devolucao(chave_independente(f'321905{FORN}55001000000999112345678'), [emit])
            t('chave sem XML no portal avisa', False)
        except nfe.NfeInvalida as exc:
            t('chave sem XML no portal avisa', 'XML completo' in str(exc))

        print('\n== SIMPLES NACIONAL (emitente do RJ) ==')
        sn = nova_nota(emit_rj, dest_uf='RJ', dest_codigo_municipio='3304557', dest_municipio='Rio de Janeiro', itens=[
            dict(codigo='C3', descricao='ZZ CABO', ncm='85444200', cfop='5102', quantidade=Decimal('3'),
                 valor_unitario=Decimal('9.9'), cst_icms='102')])
        sn = nfe.enviar(sn)
        xs = etree.fromstring(sn.xml_autorizado.encode())
        t('CSOSN 102 sai em ICMSSN102 e CRT 1', xs.find(f'.//{{{NS}}}ICMSSN102/{{{NS}}}CSOSN').text == '102'
          and xs.find(f'.//{{{NS}}}CRT').text == '1')
        t('vai para a SEFAZ do RJ (SVRS)', sefaz.chamadas[-1] == ('autorizacao', 'RJ', '2'))
        t('certificado da matriz assina a filial (mesma raiz)', sn.status == 'AUTORIZADA')
        t('vUnCom com as casas que precisa', xs.find(f'.//{{{NS}}}vUnCom').text == '9.90')
        t('endereços: SVRS, SP e produção', nfe.url_do_servico('autorizacao', 'SP', '1').startswith('https://nfe.fazenda.sp.gov.br/')
          and 'homologacao' in nfe.url_do_servico('evento', 'ES', '2'))

        print('\n== TELAS ==')
        chefe = User.objects.create_user(username='zzz.nfechefe', email='zzz.nfechefe@exemplo-teste.local',
                                         password='S3nha!teste', first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN')
        c = Client(HTTP_USER_AGENT='ZZ-Teste', REMOTE_ADDR='10.1.1.9')
        c.force_login(chefe)
        r = c.get('/financeiro/notas/')
        t('lista abre', r.status_code == 200 and 'Notas fiscais' in r.content.decode())
        t('aba "Notas fiscais" no Financeiro', 'href="/financeiro/notas/"' in r.content.decode())
        r = c.get('/financeiro/notas/configuracao/')
        t('configuração abre para o SUPERADMIN', r.status_code == 200 and 'Certificado digital A1' in r.content.decode())
        t('a tela não devolve o certificado', config.certificado_cifrado[:30] not in r.content.decode())

        config.ligado = False
        config.save()
        r = c.post('/financeiro/notas/configuracao/', {'acao': 'geral', 'ligado': '1', 'ambiente': '1'})
        config.refresh_from_db()
        t('produção sem digitar PRODUCAO é recusada', config.ambiente == '2' and not config.ligado)
        c.post('/financeiro/notas/configuracao/', {'acao': 'geral', 'ligado': '1', 'ambiente': '2'})
        config.refresh_from_db()
        t('liga em homologação pela tela', config.ligado and config.ambiente == '2')
        t('mudança da configuração na rastreabilidade',
          LogFinanceiro.objects.filter(acao='CONFIGURAR', area='notas', usuario=chefe).exists())
        r = c.post('/financeiro/notas/configuracao/', {
            'acao': 'certificado', 'senha': 'senha-teste',
            'certificado': SimpleUploadedFile('zz.pfx', certificado(MATRIZ), content_type='application/x-pkcs12')})
        config.refresh_from_db()
        t('troca o certificado pela tela', config.certificado_cnpj == MATRIZ and r.status_code == 302)
        r = c.post('/financeiro/notas/configuracao/', {
            'acao': 'emitente', 'cnpj': cnpj_de('393939390003'), 'razao_social': 'ZZ REDE SP', 'ie': '111222333444',
            'crt': '3', 'logradouro': 'Rua SP', 'numero': '3', 'bairro': 'Centro', 'municipio': 'Sao Paulo', 'uf': 'SP',
            'codigo_municipio': '3304557', 'cep': '01000000', 'serie': '1', 'proximo_numero': '1',
            'proximo_numero_homologacao': '1'})
        t('emitente com município de outra UF é recusado', not EmitenteNFe.objects.filter(cnpj=cnpj_de('393939390003')).exists())
        c.post('/financeiro/notas/configuracao/', {
            'acao': 'emitente', 'cnpj': cnpj_de('393939390003'), 'razao_social': 'ZZ REDE SP', 'ie': '111222333444',
            'crt': '3', 'logradouro': 'Rua SP', 'numero': '3', 'bairro': 'Centro', 'municipio': 'Sao Paulo', 'uf': 'SP',
            'codigo_municipio': '3550308', 'cep': '01000000', 'serie': '2', 'proximo_numero': '150',
            'proximo_numero_homologacao': '1'})
        emit_sp = EmitenteNFe.objects.filter(cnpj=cnpj_de('393939390003')).first()
        t('emitente de SP cadastrado com série 2 a partir do 150', emit_sp and (emit_sp.serie, emit_sp.proximo_numero) == (2, 150))

        r = c.get('/financeiro/notas/nova/?tipo=devolucao&devolver=' + ch_forn)
        corpo = r.content.decode()
        t('tela de devolução já vem com itens e CFOP 5202', r.status_code == 200 and 'value="5202"' in corpo
          and 'ZZ CAPA SILICONE' in corpo)
        t('quantidade na tela com vírgula (10)', 'name="item_quantidade" inputmode="decimal" class="fin-input nfe-num" value="10"' in corpo)
        formulario = {
            'emitente': emit.pk, 'tipo_operacao': '1', 'finalidade': '4', 'natureza': 'Devolucao de compra',
            'chave_referenciada': ch_forn, 'dest_documento': f'{FORN[:2]}.{FORN[2:5]}.{FORN[5:8]}/{FORN[8:12]}-{FORN[12:]}',
            'dest_nome': 'ZZ DISTRIBUIDORA LTDA', 'dest_ie': '082000001', 'dest_ind_ie': '1', 'dest_logradouro': 'Rua A',
            'dest_numero': '10', 'dest_bairro': 'Centro', 'dest_codigo_municipio': '3205309', 'dest_municipio': 'Vitoria',
            'dest_uf': 'ES', 'dest_cep': '29000-000', 'presenca': '0', 'modalidade_frete': '9', 'forma_pagamento': '90',
            'valor_frete': '0', 'valor_seguro': '0', 'valor_outras': '0', 'info_complementar': 'Mercadoria com defeito',
            'item_codigo': ['A1', ''], 'item_descricao': ['ZZ CAPA SILICONE', ''], 'item_ean': ['', ''],
            'item_ncm': ['3926.90.90', ''], 'item_cest': ['', ''], 'item_cfop': ['5202', ''], 'item_unidade': ['un', ''],
            'item_quantidade': ['2', ''], 'item_valor_unitario': ['15,00', ''], 'item_valor_desconto': ['0', ''],
            'item_origem': ['0', '0'], 'item_cst_icms': ['00', '00'], 'item_reducao_bc_icms': ['0', ''],
            'item_aliquota_icms': ['17', ''], 'item_cst_pis': ['49', '49'], 'item_aliquota_pis': ['0', ''],
            'item_cst_cofins': ['49', '49'], 'item_aliquota_cofins': ['0', ''], 'item_info_adicional': ['', ''],
        }
        antes_n = NotaFiscalEmitida.objects.count()
        r = c.post('/financeiro/notas/nova/', formulario)
        nova = NotaFiscalEmitida.objects.order_by('-id').first()
        t('rascunho criado pela tela', NotaFiscalEmitida.objects.count() == antes_n + 1 and r.status_code == 302)
        t('linha vazia de item é ignorada; números com vírgula e máscara limpos',
          nova.itens.count() == 1 and nova.itens.first().ncm == '39269090' and nova.dest_documento == FORN
          and nova.valor_total == Decimal('30.00') and nova.dest_cep == '29000000')
        t('rascunho na rastreabilidade', LogFinanceiro.objects.filter(acao='CRIAR', area='notas', objeto_id=str(nova.pk)).exists())
        r = c.get(f'/financeiro/notas/{nova.pk}/')
        t('tela da nota abre sem pendências', r.status_code == 200 and 'Antes de enviar' not in r.content.decode())
        r = c.get(f'/financeiro/notas/{nova.pk}/danfe/')
        t('prévia da DANFE em PDF', r.status_code == 200 and r['Content-Type'] == 'application/pdf' and r.content[:5] == b'%PDF-')
        r = c.get(f'/financeiro/notas/{nova.pk}/xml/')
        t('rascunho não tem XML para baixar', r.status_code == 404)
        antes_chamadas = len(sefaz.chamadas)
        r = c.post(f'/financeiro/notas/{nova.pk}/enviar/')
        nova.refresh_from_db()
        t('enviada pela tela e autorizada', nova.status == 'AUTORIZADA' and len(sefaz.chamadas) == antes_chamadas + 1)
        t('envio na rastreabilidade', LogFinanceiro.objects.filter(acao='EMITIR', objeto_id=str(nova.pk)).exists())
        r = c.get(f'/financeiro/notas/{nova.pk}/xml/')
        t('baixa o XML autorizado', r.status_code == 200 and f'{nova.chave}-procNFe.xml' in r['Content-Disposition']
          and b'<protNFe' in r.content)
        r = c.get(f'/financeiro/notas/{nova.pk}/danfe/?baixar=1')
        t('baixa a DANFE', r.status_code == 200 and 'attachment' in r['Content-Disposition'])
        r = c.get(f'/financeiro/notas/{nova.pk}/editar/')
        t('autorizada não abre edição', r.status_code == 302)
        c.post(f'/financeiro/notas/{nova.pk}/correcao/', {'correcao': 'Correcao do nome do transportador para ZZ Entregas'})
        t('carta de correção pela tela', EventoNotaFiscal.objects.filter(nota=nova, tipo='110110', sucesso=True).exists())
        c.post(f'/financeiro/notas/{nova.pk}/cancelar/', {'justificativa': 'Devolucao feita por engano, mercadoria ficou'})
        nova.refresh_from_db()
        t('cancelamento pela tela', nova.status == 'CANCELADA')
        evento = nova.eventos.filter(tipo='110111').first()
        r = c.get(f'/financeiro/notas/evento/{evento.pk}/xml/')
        t('baixa o XML do evento', r.status_code == 200 and b'procEventoNFe' in r.content)
        r = c.get('/financeiro/notas/?status=CANCELADA')
        t('filtro por situação', r.status_code == 200 and f'Nº {nova.numero}' in r.content.decode())
        r = c.get('/financeiro/notas/?q=' + FORN)
        t('busca por CNPJ', r.status_code == 200 and 'ZZ DISTRIBUIDORA' in r.content.decode())
        rascunho = nova_nota(emit)
        c.post(f'/financeiro/notas/{rascunho.pk}/excluir/')
        t('rascunho nunca enviado pode ser excluído', not NotaFiscalEmitida.objects.filter(pk=rascunho.pk).exists())
        c.post(f'/financeiro/notas/{n2.pk}/excluir/')
        t('nota numerada não se exclui', NotaFiscalEmitida.objects.filter(pk=n2.pk).exists())

        print('\n== MENU LATERAL ==')
        r = c.get('/financeiro/notas/')
        corpo = r.content.decode()
        t('menu lateral agrupado (Contas, Caixa e bancos, Fiscal, Gestão)',
          all(f'<span>{g}</span>' in corpo for g in ('Contas', 'Caixa e bancos', 'Fiscal', 'Gestão')))
        t('item da tela atual marcado no menu', 'class="fin-menu-item fin-menu-ativo" data-dica="Notas fiscais" aria-current="page"' in corpo)
        t('celular: barra com a tela atual abre a folha do menu', 'data-abrir="#finMenuFolha"' in corpo and 'id="finMenuFolha"' in corpo)
        t('barra de abas horizontal saiu', 'fin-abas-lista' not in corpo and 'fin-tab-ind' not in corpo)
        t('SUPERADMIN vê Acessos no menu', 'href="/financeiro/acessos/"' in corpo)

        print('\n== IMPORTAR XML PARA A LISTA ==')
        t('lista de notas tem a zona de importar XML', 'id="nfeXmlZona"' in corpo and '/financeiro/notas/importar-xml/' in corpo
          and 'multiple' in corpo)

        def subir(cliente, *arquivos, json=True):
            extra = {'HTTP_ACCEPT': 'application/json'} if json else {}
            files = [SimpleUploadedFile(nome, conteudo.encode() if isinstance(conteudo, str) else conteudo, 'text/xml')
                     for nome, conteudo in arquivos]
            return cliente.post('/financeiro/notas/importar-xml/', {'xml': files}, **extra)

        def autorizado(xml, ch, cstat='100'):
            return xml.replace('<protNFe><infProt><nProt>132190000000001</nProt></infProt></protNFe>',
                               f'<protNFe versao="4.00"><infProt><tpAmb>2</tpAmb><chNFe>{ch}</chNFe>'
                               f'<dhRecbto>2019-05-03T09:01:00-03:00</dhRecbto><nProt>132190000000001</nProt>'
                               f'<cStat>{cstat}</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe>')

        compra_xml = autorizado(proc_nfe_fornecedor(ch_forn), ch_forn).replace(
            '<vICMS>25.50</vICMS></ICMS00></ICMS></imposto>',
            '<vICMS>25.50</vICMS></ICMS00></ICMS><IPI><cEnq>999</cEnq><IPITrib><CST>50</CST><vBC>150.00</vBC>'
            '<pIPI>5.00</pIPI><vIPI>7.50</vIPI></IPITrib></IPI><PIS><PISAliq><CST>01</CST><vBC>150.00</vBC><pPIS>1.65</pPIS>'
            '<vPIS>2.48</vPIS></PISAliq></PIS></imposto>')
        r = subir(c, ('compra-801.xml', compra_xml))
        j = r.json()
        rec = NotaFiscalEmitida.objects.filter(chave=ch_forn).first()
        t('XML de compra entra na lista como RECEBIDA e autorizada', r.status_code == 200 and j['conta']['importada'] == 1
          and rec is not None and (rec.origem, rec.status, rec.protocolo) == ('TERCEIRO', 'AUTORIZADA', '132190000000001'), j)
        t('do nosso ponto de vista é ENTRADA; o fornecedor fica como a outra parte', rec.tipo_operacao == '0'
          and rec.dest_documento == FORN and rec.dest_nome == 'ZZ DISTRIBUIDORA LTDA' and rec.emitente_id == emit.pk
          and rec.loja_id == emit.loja_id)
        t('número, série, valores e itens do XML (sem recalcular)', (rec.numero, rec.serie, rec.valor_total, rec.valor_icms)
          == (801, 1, Decimal('150.00'), Decimal('25.50')) and rec.itens.count() == 1
          and rec.itens.first().valor_pis == Decimal('2.48') and rec.itens.first().cfop == '5102')
        t('datas de emissão e autorização lidas', rec.emitida_em and rec.emitida_em.year == 2019 and rec.autorizada_em is not None)
        t('o XML fica guardado e a importação na rastreabilidade', rec.xml_autorizado.startswith('<nfeProc')
          and LogFinanceiro.objects.filter(area='notas', acao='CRIAR', objeto_id=str(rec.pk)).exists())
        r = subir(c, ('de-novo.xml', compra_xml))
        t('importar a mesma nota de novo não duplica', r.json()['conta'] == {'importada': 0, 'completada': 0, 'repetida': 1, 'cancelada': 0, 'erro': 0}
          and NotaFiscalEmitida.objects.filter(chave=ch_forn).count() == 1)
        r = c.get('/financeiro/notas/?origem=TERCEIRO')
        corpo = r.content.decode()
        t('lista mostra a recebida com o selo e filtra por origem', 'nfe-origem-recebida' in corpo and 'ZZ DISTRIBUIDORA' in corpo
          and f'/financeiro/notas/{n2.pk}/' not in corpo)
        r = c.get(f'/financeiro/notas/{rec.pk}/')
        corpo = r.content.decode()
        t('tela da recebida: fornecedor como emitente, sem cancelar/CC-e/consultar, com devolução',
          r.status_code == 200 and 'Emitente (fornecedor)' in corpo and 'Devolver ao fornecedor' in corpo
          and 'Cancelar na SEFAZ' not in corpo and 'Registrar correção' not in corpo and 'Consultar na SEFAZ' not in corpo
          and 'Tirar da lista' in corpo and f'?copiar={rec.pk}' not in corpo)
        r = c.get(f'/financeiro/notas/{rec.pk}/danfe/')
        t('DANFE da nota recebida (com IPI no XML)', r.status_code == 200 and r.content[:5] == b'%PDF-')
        r = c.get(f'/financeiro/notas/nova/?tipo=devolucao&devolver={ch_forn}')
        t('devolução a partir da recebida já vem com CFOP 5202', r.status_code == 200 and 'value="5202"' in r.content.decode())
        antes_chamadas = len(sefaz.chamadas)
        c.post(f'/financeiro/notas/{rec.pk}/cancelar/', {'justificativa': 'Tentativa de cancelar nota importada'})
        c.post(f'/financeiro/notas/{rec.pk}/consultar/')
        rec.refresh_from_db()
        t('nota importada não vai para a SEFAZ (cancelar, consultar)', rec.status == 'AUTORIZADA'
          and len(sefaz.chamadas) == antes_chamadas and not rec.eventos.exists())

        # Nota nossa emitida em outro sistema (mesmo CNPJ, outra chave, número alto na série do portal).
        ch_fora = chave_independente(f'322611{MATRIZ}55001000000900187654321')
        fora_xml = n2.xml_autorizado.replace(n2.chave, ch_fora).replace(f'<nNF>{n2.numero}</nNF>', '<nNF>900</nNF>')
        r = subir(c, ('fora.xml', fora_xml), ('compra-de-novo.xml', compra_xml), ('lixo.xml', 'nao e xml'))
        j = r.json()
        fora = NotaFiscalEmitida.objects.filter(chave=ch_fora).first()
        t('vários arquivos de uma vez: resultado por arquivo', [x['situacao'] for x in j['resultados']] == ['importada', 'repetida', 'erro'], j)
        t('nota nossa de outro sistema entra como IMPORTADA, saída, com o cliente', fora is not None
          and (fora.origem, fora.tipo_operacao, fora.numero, fora.dest_documento) == ('PROPRIA', '1', 900, CLIENTE))
        t('avisa que o número usa a série do portal acima do próximo número', any('Configuração' in a for a in j['resultados'][0]['avisos']))
        t('erro do arquivo ruim diz o motivo', 'não é um XML válido' in j['resultados'][2]['erro'])
        r = c.get(f'/financeiro/notas/{fora.pk}/')
        corpo = r.content.decode()
        t('tela da importada: copiar e receber devolução; sem cancelar pela SEFAZ', 'nfe-origem-propria' in corpo
          and f'?copiar={fora.pk}' in corpo and 'Receber devolução' in corpo and 'Cancelar na SEFAZ' not in corpo
          and 'importe aqui o XML do cancelamento' in corpo)
        repetido = n2.xml_autorizado.replace(n2.chave, chave_independente(f'322611{MATRIZ}55001{n2.numero:09d}112345678'))
        r = subir(c, ('mesmo-numero.xml', repetido))
        t('mesmo número da série com outra chave é recusado', 'já existe na lista' in r.json()['resultados'][0].get('erro', ''),
          r.json())

        cancel = (f'<procEventoNFe xmlns="{NS}" versao="1.00"><evento versao="1.00"><infEvento Id="ID110111{ch_fora}01">'
                  f'<cOrgao>32</cOrgao><tpAmb>2</tpAmb><CNPJ>{MATRIZ}</CNPJ><chNFe>{ch_fora}</chNFe>'
                  f'<dhEvento>2026-11-20T10:00:00-03:00</dhEvento><tpEvento>110111</tpEvento><nSeqEvento>1</nSeqEvento>'
                  f'</infEvento></evento><retEvento versao="1.00"><infEvento><cStat>135</cStat>'
                  f'<xMotivo>Evento registrado e vinculado a NF-e</xMotivo><chNFe>{ch_fora}</chNFe>'
                  f'<dhRegEvento>2026-11-20T10:00:05-03:00</dhRegEvento></infEvento></retEvento></procEventoNFe>')
        r = subir(c, ('cancelamento.xml', cancel))
        fora.refresh_from_db()
        t('XML do cancelamento marca a importada como cancelada', r.json()['conta']['cancelada'] == 1 and fora.status == 'CANCELADA'
          and fora.cancelada_em is not None)
        r = subir(c, ('cancel-portal.xml', cancel.replace(ch_fora, n2.chave)))
        n2.refresh_from_db()
        t('cancelamento importado não mexe em nota emitida pelo portal', n2.status == 'AUTORIZADA' and r.json()['conta']['erro'] == 1)

        import io
        import zipfile
        pacote = io.BytesIO()
        with zipfile.ZipFile(pacote, 'w') as z:
            ch_z = chave_independente(f'321905{FORN}55001000000802112345678')
            z.writestr('notas/802.xml', autorizado(proc_nfe_fornecedor(ch_z), ch_z))
            z.writestr('leia-me.txt', 'nada')
            z.writestr('notas/sem-protocolo.xml', proc_nfe_fornecedor(chave_independente(f'321905{FORN}55001000000803112345678')))
        r = subir(c, ('lote.zip', pacote.getvalue()))
        j = r.json()
        t('ZIP: importa os XMLs de dentro e ignora o resto', [x['situacao'] for x in j['resultados']] == ['importada', 'erro']
          and NotaFiscalEmitida.objects.filter(chave=ch_z, origem='TERCEIRO').exists(), j)
        t('XML sem protocolo de autorização é recusado com explicação', 'protocolo de autorização' in j['resultados'][1]['erro'])
        r = subir(c, ('nota.pdf', '<x/>'))
        t('extensão errada recusada', r.json()['resultados'][0]['situacao'] == 'erro' and '.zip' in r.json()['resultados'][0]['erro'])
        r = subir(c, ('ct.xml', '<cteProc xmlns="http://www.portalfiscal.inf.br/cte"><CTe><infCte Id="CTe1"/></CTe></cteProc>'))
        t('XML de CT-e recusado', 'CT-e' in r.json()['resultados'][0]['erro'])
        estranha = autorizado(proc_nfe_fornecedor(ch_z), ch_z).replace(f'<CNPJ>{MATRIZ}</CNPJ>', f'<CNPJ>{cnpj_de("777777770001")}</CNPJ>')
        OUTRO_CNPJ = cnpj_de('777777770001')
        ch_e = chave_independente(f'321905{FORN}55001000000810112345678')
        estranha = autorizado(proc_nfe_fornecedor(ch_e), ch_e).replace(f'<CNPJ>{MATRIZ}</CNPJ>', f'<CNPJ>{OUTRO_CNPJ}</CNPJ>')
        j = subir(c, ('sem-emitente.xml', estranha)).json()
        sem = NotaFiscalEmitida.objects.filter(chave=ch_e).first()
        t('sem emitente cadastrado a nota entra assim mesmo (recebida, sem loja)', j['conta']['importada'] == 1 and sem is not None
          and (sem.origem, sem.emitente_id, sem.loja_id, sem.nosso_cnpj) == ('TERCEIRO', None, None, OUTRO_CNPJ), j)
        t('…e avisa que o CNPJ não é emitente cadastrado', any('não é emitente cadastrado' in a for a in j['resultados'][0]['avisos']))
        r = c.get(f'/financeiro/notas/{sem.pk}/')
        corpo = r.content.decode()
        t('tela da nota sem emitente abre (nosso CNPJ do XML, sem devolução nem consulta)', r.status_code == 200
          and 'ainda não cadastrado como emitente' in corpo and 'Devolver ao fornecedor' not in corpo
          and 'Consultar na SEFAZ' not in corpo and c.get(f'/financeiro/notas/{sem.pk}/danfe/').status_code == 200)
        r = c.get('/financeiro/notas/')
        t('lista mostra a nota sem loja com o CNPJ', r.status_code == 200 and 'sem loja' in r.content.decode())
        FILIAL_NOVA = MATRIZ[:8] + '009900'
        ch_f = chave_independente(f'322611{MATRIZ}55001000000777187654321')
        filial_xml = (n2.xml_autorizado.replace(n2.chave, ch_f).replace(f'<nNF>{n2.numero}</nNF>', '<nNF>777</nNF>')
                      .replace(f'<CNPJ>{MATRIZ}</CNPJ>', f'<CNPJ>{FILIAL_NOVA}</CNPJ>', 1))
        j = subir(c, ('filial.xml', filial_xml)).json()
        fil = NotaFiscalEmitida.objects.filter(chave=ch_f).first()
        t('CNPJ da mesma raiz da empresa como emitente = nota nossa (importada) sem emitente',
          fil is not None and (fil.origem, fil.emitente_id, fil.nosso_cnpj) == ('PROPRIA', None, FILIAL_NOVA), j)
        nova_filial = EmitenteNFe.objects.create(loja=loja_es, cnpj=FILIAL_NOVA, razao_social='ZZ REDE FILIAL', ie='083000099',
                                                 crt='3', logradouro='Rua F', numero='9', bairro='Centro',
                                                 codigo_municipio='3205309', municipio='Vitoria', uf='ES', cep='29000000')
        fil.refresh_from_db()
        t('cadastrar o CNPJ como emitente puxa as notas importadas para ele e a loja', fil.emitente_id == nova_filial.pk
          and fil.loja_id == loja_es.pk)
        xxe = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
               + compra_xml.replace('<natOp>VENDA</natOp>', '<natOp>&e;</natOp>'))
        r = subir(c, ('xxe.xml', xxe))
        t('entidade externa no XML não é lida (XXE)', 'root:' not in r.content.decode()
          and not NotaFiscalEmitida.objects.filter(natureza__contains='root:').exists())
        ch_s = chave_independente(f'321905{FORN}55001000000804112345678')
        r = subir(c, ('so-uma.xml', autorizado(proc_nfe_fornecedor(ch_s), ch_s)), json=False)
        so = NotaFiscalEmitida.objects.get(chave=ch_s)
        t('sem JavaScript, uma nota leva direto para a tela dela', r.status_code == 302 and r['Location'] == f'/financeiro/notas/{so.pk}/')
        c.post(f'/financeiro/notas/{so.pk}/excluir/')
        t('importada pode sair da lista (fica na rastreabilidade)', not NotaFiscalEmitida.objects.filter(pk=so.pk).exists()
          and LogFinanceiro.objects.filter(area='notas', acao='EXCLUIR', objeto_id=str(so.pk)).exists())

        copia = nfe.dados_do_xml(n2.xml_autorizado, [emit], 'copia')
        t('dados_do_xml (cópia) mantém saída, CST e PIS/Cofins', (copia['nota']['tipo_operacao'], copia['nota']['finalidade'])
          == ('1', '1') and copia['itens'][0]['cst_pis'] == n2.itens.first().cst_pis)
        dev_venda = nfe.dados_do_xml(n2.xml_autorizado, [emit], 'devolucao')
        t('devolução da venda pelo XML = entrada 1202 referenciando a chave', dev_venda['nota']['tipo_operacao'] == '0'
          and dev_venda['itens'][0]['cfop'] == '1202' and dev_venda['nota']['chave_referenciada'] == n2.chave)
        t('formulário de devolução sobe o XML e abre a devolução', 'data-nfe-xml-abrir' in c.get('/financeiro/notas/nova/?tipo=devolucao').content.decode())

        print('\n== PUXAR DA SEFAZ (PIS/Cofins) ==')
        from datetime import timezone as tz
        quando = datetime(2019, 5, 3, 12, 0, tzinfo=tz.utc)
        def recebida(numero, xml_completo=True, situacao='AUTORIZADA', cnpj=MATRIZ):
            ch = chave_independente(f'321905{FORN}55001{numero:09d}112345678')
            completo = autorizado(proc_nfe_fornecedor(ch), ch).replace(f'<CNPJ>{MATRIZ}</CNPJ>', f'<CNPJ>{cnpj}</CNPJ>')
            return NotaRecebida.objects.create(
                chave=ch, cnpj_destinatario=cnpj, emitente_cnpj=FORN, emitente_nome='ZZ DISTRIBUIDORA LTDA', emitente_ie='082000001',
                modelo='55', serie='1', numero=str(numero), valor=Decimal('150'), emissao=quando, situacao=situacao, tipo_operacao='1',
                xml_resumo=(f'<resNFe xmlns="{NS}" versao="1.01"><chNFe>{ch}</chNFe><nProt>1352600000{numero}</nProt>'
                            f'<dhRecbto>2019-05-03T09:01:00-03:00</dhRecbto></resNFe>'),
                xml_completo=completo if xml_completo else '')
        r_ok, r_resumo, r_cancel = recebida(901), recebida(902, xml_completo=False), recebida(903, situacao='CANCELADA')
        r_nfse = NotaRecebida.objects.create(chave='9' * 50, tipo_documento='NFSE', cnpj_destinatario=MATRIZ, emitente_cnpj=FORN,
                                             emissao=quando, valor=Decimal('10'))
        r_rj = recebida(904, cnpj=FILIAL_RJ)
        r = c.get('/financeiro/notas/?competencia=2019-05')
        corpo = r.content.decode()
        t('lista mostra o cartão da SEFAZ com o que falta puxar do mês', 'Puxar da SEFAZ' in corpo
          and '4 notas do mês ainda fora da lista' in corpo, corpo[corpo.find('nfeSefaz'):corpo.find('nfeSefaz') + 1500][-400:])
        r = c.post('/financeiro/notas/importar-sefaz/', {'competencia': '2019-05'})
        msgs = [str(m) for m in r.wsgi_request._messages]
        ok_n = NotaFiscalEmitida.objects.filter(chave=r_ok.chave).first()
        res_n = NotaFiscalEmitida.objects.filter(chave=r_resumo.chave).first()
        can_n = NotaFiscalEmitida.objects.filter(chave=r_cancel.chave).first()
        t('puxar da SEFAZ traz as NF-e do mês (XML completo vira recebida com itens)', r.status_code == 302 and ok_n is not None
          and ok_n.origem == 'TERCEIRO' and ok_n.itens.count() == 1 and ok_n.emitente_id == emit.pk, msgs)
        t('nota só com resumo entra sem itens, marcada "só resumo"', res_n is not None and res_n.so_resumo and res_n.itens.count() == 0
          and (res_n.numero, res_n.valor_total, res_n.tipo_operacao, res_n.dest_documento) == (902, Decimal('150.00'), '0', FORN)
          and res_n.protocolo == '1352600000902')
        t('cancelada na SEFAZ entra cancelada', can_n is not None and can_n.status == 'CANCELADA')
        t('NFS-e fica só no PIS/Cofins', not NotaFiscalEmitida.objects.filter(chave=r_nfse.chave).exists())
        t('mensagem diz quantas e o que falta (ciência)', any('manifeste ciência' in m for m in msgs), msgs)
        t('volta para a lista das recebidas', r['Location'].endswith('?origem=TERCEIRO'))
        r = c.get(f'/financeiro/notas/{res_n.pk}/')
        corpo = r.content.decode()
        t('tela da nota só com resumo: sem DANFE, explica a ciência', r.status_code == 200 and 'Só o resumo da SEFAZ' in corpo
          and f'/financeiro/notas/{res_n.pk}/danfe/' not in corpo and c.get(f'/financeiro/notas/{res_n.pk}/danfe/').status_code == 404)
        r = c.get('/financeiro/notas/?competencia=2019-05')
        t('depois de puxar, nada do mês fora da lista', 'Tudo do mês já está na lista' in r.content.decode())
        NotaRecebida.objects.filter(pk=r_resumo.pk).update(xml_completo=autorizado(proc_nfe_fornecedor(r_resumo.chave), r_resumo.chave))
        t('chegou o XML completo: a nota volta a contar como pendente',
          '1 nota do mês ainda fora da lista' in c.get('/financeiro/notas/?competencia=2019-05').content.decode())
        r = c.post('/financeiro/notas/importar-sefaz/', {'competencia': '2019-05', 'voltar': '/financeiro/pis-cofins/?competencia=2019-05&visao=sefaz'})
        res_n2 = NotaFiscalEmitida.objects.get(chave=r_resumo.chave)
        t('puxar de novo completa a mesma linha (itens e XML)', res_n2.pk == res_n.pk and not res_n2.so_resumo
          and res_n2.itens.count() == 1 and NotaFiscalEmitida.objects.filter(chave=r_resumo.chave).count() == 1)
        t('volta para a visão SEFAZ do PIS/Cofins quando veio de lá', r['Location'] == '/financeiro/pis-cofins/?competencia=2019-05&visao=sefaz')
        r = c.get('/financeiro/pis-cofins/?competencia=2019-05&visao=sefaz')
        corpo = r.content.decode()
        t('PIS/Cofins (visão SEFAZ) tem o botão de enviar para Notas fiscais', r.status_code == 200
          and ('pcParaNotas' in corpo or 'pcBuscarSefaz' not in corpo))
        t('importações da SEFAZ na rastreabilidade', LogFinanceiro.objects.filter(area='notas', descricao__contains='Puxou da SEFAZ').exists())

        print('\n== QUEM PODE ==')
        so_ver = User.objects.create_user(username='zzz.nfever', email='zzz.nfever@exemplo-teste.local',
                                          password='S3nha!teste', first_name='ZZ', hierarchy='SUPERVISOR')
        acesso = AcessoFinanceiro.objects.create(user=so_ver, permissoes=['notas.ver'])
        acesso.lojas.set([loja_rj])
        cv = Client()
        cv.force_login(so_ver)
        r = cv.get('/financeiro/notas/')
        t('quem tem notas.ver abre a lista', r.status_code == 200)
        t('…e só vê as notas das suas lojas', f'/financeiro/notas/{rec.pk}/' not in r.content.decode()
          and f'/financeiro/notas/{nova.pk}/' not in r.content.decode() and f'/financeiro/notas/{sem.pk}/' not in r.content.decode())
        t('nota de outra loja dá 404', cv.get(f'/financeiro/notas/{nova.pk}/').status_code == 404)
        t('vê a nota da sua loja', cv.get(f'/financeiro/notas/{sn.pk}/').status_code == 200)
        antes_chamadas = len(sefaz.chamadas)
        rj2 = nova_nota(emit_rj, dest_uf='RJ', dest_codigo_municipio='3304557', dest_municipio='Rio de Janeiro', itens=[
            dict(codigo='C3', descricao='ZZ CABO', ncm='85444200', cfop='5102', quantidade=Decimal('1'),
                 valor_unitario=Decimal('9.9'), cst_icms='102')])
        r = cv.post(f'/financeiro/notas/{rj2.pk}/enviar/')
        rj2.refresh_from_db()
        t('sem notas.emitir não envia', rj2.status == 'RASCUNHO' and len(sefaz.chamadas) == antes_chamadas)
        r = cv.post('/financeiro/notas/importar-xml/', {'xml': SimpleUploadedFile('a.xml', compra_xml.encode())},
                    HTTP_ACCEPT='application/json')
        t('sem notas.emitir não importa XML', r.status_code in (302, 403))
        t('sem notas.emitir não vê a zona de importar', 'id="nfeXmlZona"' not in cv.get('/financeiro/notas/').content.decode())
        cv.post(f'/financeiro/notas/{sn.pk}/cancelar/', {'justificativa': 'Tentativa sem permissao de cancelar'})
        sn.refresh_from_db()
        t('sem notas.cancelar não cancela', sn.status == 'AUTORIZADA')
        t('configuração só do SUPERADMIN', cv.get('/financeiro/notas/configuracao/').status_code == 302)
        acesso.permissoes = ['notas.emitir']
        acesso.save()
        cv = Client()
        cv.force_login(so_ver)
        r = cv.get('/financeiro/notas/nova/')
        r_rj2, r_es2 = recebida(905, cnpj=FILIAL_RJ), recebida(906)
        cv.post('/financeiro/notas/importar-sefaz/', {'competencia': '2019-05'})
        t('quem só vê a loja RJ puxa da SEFAZ só as notas do CNPJ dela', NotaFiscalEmitida.objects.filter(
          chave=r_rj2.chave, emitente=emit_rj).exists() and not NotaFiscalEmitida.objects.filter(chave=r_es2.chave).exists())
        t('quem emite só escolhe emitente das suas lojas', r.status_code == 200 and FILIAL_RJ[:2] in r.content.decode()
          and emit.cnpj_formatado not in r.content.decode())
        sem = User.objects.create_user(username='zzz.nfesem', email='zzz.nfesem@exemplo-teste.local',
                                       password='S3nha!teste', first_name='ZZ', hierarchy='SUPERVISOR')
        cs = Client()
        cs.force_login(sem)
        t('sem acesso ao Financeiro não entra', cs.get('/financeiro/notas/').status_code == 302)

    print('\n== TRANSPORTE REAL TRAVADO ==')
    # Fora do patch: o transporte real recusa com a emissão desligada antes de abrir conexão.
    with mock.patch.object(requests, 'post', sem_rede):
        try:
            nfe._transporte_https('status', 'ES', '2', '<x/>', ConfiguracaoNFe(ligado=False))
            t('transporte real recusa com a emissão desligada', False)
        except nfe.NfeInvalida:
            t('transporte real recusa com a emissão desligada', True)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco; nada enviado à SEFAZ.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
