"""Ler o XML da nota para mostrar na tela (o "DANFE" do portal).

NF-e: emitente, destinatário, itens, impostos, totais, duplicatas, pagamento,
transporte e informações complementares, direto do XML completo (procNFe).
Com só o resumo (resNFe), a tela mostra o que o resumo tem e explica que o
XML completo vem depois da ciência da operação.

NFS-e (Ambiente Nacional): prestador, tomador, serviço e valores.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_datetime
from lxml import etree

FORMAS_PAGAMENTO = {
    '01': 'Dinheiro', '02': 'Cheque', '03': 'Cartão de crédito', '04': 'Cartão de débito', '05': 'Crédito loja',
    '10': 'Vale alimentação', '11': 'Vale refeição', '12': 'Vale presente', '13': 'Vale combustível',
    '15': 'Boleto bancário', '16': 'Depósito bancário', '17': 'PIX', '18': 'Transferência / carteira digital',
    '19': 'Fidelidade / cashback', '90': 'Sem pagamento', '99': 'Outros',
}
MODALIDADES_FRETE = {'0': 'Por conta do emitente', '1': 'Por conta do destinatário', '2': 'Por conta de terceiros',
                     '3': 'Próprio, do remetente', '4': 'Próprio, do destinatário', '9': 'Sem frete'}


def _raiz(xml):
    if not xml:
        return None
    try:
        raiz = etree.fromstring(xml.encode('utf-8') if isinstance(xml, str) else xml)
    except etree.XMLSyntaxError:
        return None
    for el in raiz.iter():
        if isinstance(el.tag, str) and '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    return raiz


def _t(el, caminho):
    if el is None:
        return ''
    achado = el.find(caminho)
    return (achado.text or '').strip() if achado is not None and achado.text else ''


def _d(el, caminho):
    try:
        texto = _t(el, caminho)
        return Decimal(texto) if texto else None
    except InvalidOperation:
        return None


def _data(texto):
    if not texto:
        return None
    valor = parse_datetime(texto) if 'T' in texto else None
    if valor:
        return valor
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        return None


def _endereco(el):
    if el is None:
        return ''
    partes = [_t(el, 'xLgr'), _t(el, 'nro'), _t(el, 'xCpl'), _t(el, 'xBairro')]
    cidade = ' / '.join(x for x in (_t(el, 'xMun'), _t(el, 'UF')) if x)
    cep = _t(el, 'CEP')
    texto = ', '.join(x for x in partes if x)
    if cidade:
        texto += f' — {cidade}'
    if cep:
        texto += f' · CEP {cep[:5]}-{cep[5:]}'
    return texto


def _pessoa(el, endereco_tag):
    if el is None:
        return None
    return {'nome': _t(el, 'xNome'), 'fantasia': _t(el, 'xFant'), 'cnpj': _t(el, 'CNPJ') or _t(el, 'CPF'),
            'ie': _t(el, 'IE'), 'endereco': _endereco(el.find(endereco_tag)), 'fone': _t(el, f'{endereco_tag}/fone'),
            'email': _t(el, 'email')}


def ler_nfe(xml):
    raiz = _raiz(xml)
    if raiz is None:
        return None
    inf = raiz.find('.//infNFe')
    if inf is None:
        return None
    ide = inf.find('ide')
    total = inf.find('total/ICMSTot')
    itens = []
    for det in inf.findall('det'):
        prod = det.find('prod')
        imposto = det.find('imposto')
        icms = imposto.find('ICMS')[0] if imposto is not None and imposto.find('ICMS') is not None and len(imposto.find('ICMS')) else None
        itens.append({
            'n': det.get('nItem'), 'codigo': _t(prod, 'cProd'), 'descricao': _t(prod, 'xProd'), 'ncm': _t(prod, 'NCM'),
            'cfop': _t(prod, 'CFOP'), 'unidade': _t(prod, 'uCom'), 'quantidade': _d(prod, 'qCom'),
            'unitario': _d(prod, 'vUnCom'), 'total': _d(prod, 'vProd'), 'desconto': _d(prod, 'vDesc'),
            'cst': (_t(icms, 'CST') or _t(icms, 'CSOSN')) if icms is not None else '',
            'icms': _d(icms, 'vICMS') if icms is not None else None,
            'ipi': _d(imposto, 'IPI/IPITrib/vIPI') if imposto is not None else None,
            'info': _t(det, 'infAdProd'),
        })
    duplicatas = [{'numero': _t(d, 'nDup'), 'vencimento': _data(_t(d, 'dVenc')), 'valor': _d(d, 'vDup')}
                  for d in inf.findall('cobr/dup')]
    pagamentos = [{'forma': FORMAS_PAGAMENTO.get(_t(p, 'tPag'), _t(p, 'tPag')), 'valor': _d(p, 'vPag')}
                  for p in inf.findall('pag/detPag')]
    prot = raiz.find('.//protNFe/infProt')
    transp = inf.find('transp')
    return {
        'chave': (inf.get('Id') or '').replace('NFe', ''),
        'numero': _t(ide, 'nNF'), 'serie': _t(ide, 'serie'), 'modelo': _t(ide, 'mod'),
        'natureza': _t(ide, 'natOp'), 'emissao': _data(_t(ide, 'dhEmi') or _t(ide, 'dEmi')),
        'saida': _data(_t(ide, 'dhSaiEnt')), 'tipo': 'Saída' if _t(ide, 'tpNF') == '1' else 'Entrada',
        'emitente': _pessoa(inf.find('emit'), 'enderEmit'),
        'destinatario': _pessoa(inf.find('dest'), 'enderDest'),
        'itens': itens,
        'totais': {k: _d(total, k) for k in ('vBC', 'vICMS', 'vBCST', 'vST', 'vProd', 'vFrete', 'vSeg', 'vDesc',
                                              'vII', 'vIPI', 'vPIS', 'vCOFINS', 'vOutro', 'vNF', 'vTotTrib')},
        'duplicatas': duplicatas, 'pagamentos': pagamentos,
        'frete': MODALIDADES_FRETE.get(_t(transp, 'modFrete'), ''),
        'transportadora': _t(transp, 'transporta/xNome'),
        'info_complementar': _t(inf, 'infAdic/infCpl'), 'info_fisco': _t(inf, 'infAdic/infAdFisco'),
        'protocolo': _t(prot, 'nProt'), 'autorizada_em': _data(_t(prot, 'dhRecbto')), 'status': _t(prot, 'xMotivo'),
    }


def ler_nfse(xml):
    raiz = _raiz(xml)
    if raiz is None:
        return None
    inf = raiz.find('.//infNFSe')
    if inf is None:
        return None
    dps = inf.find('.//infDPS')
    prest = dps.find('prest') if dps is not None else None
    toma = dps.find('toma') if dps is not None else None
    return {
        'numero': _t(inf, 'nNFSe').lstrip('0'), 'local': _t(inf, 'xLocEmi'), 'municipio_prestacao': _t(inf, 'xLocPrestacao'),
        'emissao': _data(_t(dps, 'dhEmi')), 'competencia': _data(_t(dps, 'dCompet')),
        'prestador': {'nome': _t(inf, 'emit/xNome'), 'cnpj': _t(inf, 'emit/CNPJ') or _t(inf, 'emit/CPF'),
                      'im': _t(inf, 'emit/IM'), 'endereco': _endereco(inf.find('emit/enderNac')),
                      'email': _t(prest, 'email'), 'fone': _t(prest, 'fone')},
        'tomador': {'nome': _t(toma, 'xNome'), 'cnpj': _t(toma, 'CNPJ') or _t(toma, 'CPF'),
                    'endereco': _endereco(toma.find('end/endNac')) if toma is not None else ''},
        'servico': ' '.join(_t(dps, 'serv/cServ/xDescServ').split()), 'tributacao': _t(inf, 'xTribNac'),
        'codigo_servico': _t(dps, 'serv/cServ/cTribNac'),
        'valor_servico': _d(dps, 'valores/vServPrest/vServ'), 'valor_liquido': _d(inf, 'valores/vLiq'),
        'iss': _d(inf, 'valores/vISSQN'), 'base_calculo': _d(inf, 'valores/vBC'),
        'aliquota': _d(inf, 'valores/pAliqAplic'),
        'retencoes': {k: _d(dps, f'valores/trib/tribFed/{c}') for k, c in
                      (('IRRF', 'vRetIRRF'), ('CSLL', 'vRetCSLL'), ('Contribuições', 'vRetCP'))},
    }


def xml_bonito(xml):
    raiz = None
    try:
        raiz = etree.fromstring(xml.encode('utf-8') if isinstance(xml, str) else xml)
    except (etree.XMLSyntaxError, AttributeError):
        return xml or ''
    return etree.tostring(raiz, pretty_print=True, encoding='unicode')
