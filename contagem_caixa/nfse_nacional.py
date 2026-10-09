"""NFS-e emitidas contra o CNPJ — Ambiente de Dados Nacional (ADN) da NFS-e.

A NFS-e não passa pela SEFAZ: o padrão nacional guarda as notas de serviço no
ADN (adn.nfse.gov.br), que tem a distribuição para o contribuinte — o mesmo
certificado A1 do e-CNPJ, a mesma ideia de NSU da NF-e:

    GET /contribuintes/DFe/{último NSU}?cnpjConsulta=<CNPJ>&lote=true

Devolve até 50 documentos a partir do NSU seguinte, cada um com o XML completo
(gzip + base64): a NFS-e já vem inteira (prestador, tomador, competência,
serviço, valores), sem precisar de ciência da operação. "Nada novo" chega como
HTTP 404 com ``NENHUM_DOCUMENTO_LOCALIZADO``.

Só entram as notas em que o CNPJ consultado é o **tomador** — é o que é compra
de serviço, o que interessa ao PIS/Cofins. As que a empresa emitiu (prestador)
são contadas e puladas. Evento de cancelamento (e101101) ou de substituição
(e105102) marca a nota como cancelada.

Limite: só aparece a nota cuja prefeitura manda para o ambiente nacional.
"""
from __future__ import annotations

import base64
import gzip
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from lxml import etree

from . import cnpj as receita
from .sefaz import SefazErro, _arquivos_pem, _cfg, _data_hora, _decimal, cnpj_do_certificado, mesma_raiz

logger = logging.getLogger(__name__)

URLS = {
    '1': 'https://adn.nfse.gov.br/contribuintes/DFe/{nsu}',
    '2': 'https://adn.producaorestrita.nfse.gov.br/contribuintes/DFe/{nsu}',
}
TIMEOUT = (10, 60)
ESPERA_SEM_NOVIDADE = timedelta(hours=1)
LOTES_POR_SINCRONIZACAO = 40                    # 40 × 50 = 2.000 documentos por rodada
EVENTOS_DE_CANCELAMENTO = ('e101101', 'e101103', 'e105102')


def _transporte_https(cnpj, nsu):
    import requests
    url = URLS.get(str(_cfg('SEFAZ_AMBIENTE', '1')), URLS['1']).format(nsu=int(nsu))
    with _arquivos_pem() as (cert, chave):
        resposta = requests.get(url, params={'cnpjConsulta': cnpj, 'lote': 'true'}, cert=(cert, chave),
                                timeout=TIMEOUT, headers={'Accept': 'application/json'})
    if resposta.status_code not in (200, 404):
        raise SefazErro(f'ADN respondeu HTTP {resposta.status_code}: {resposta.text[:300]}')
    try:
        return resposta.json()
    except ValueError as exc:
        raise SefazErro(f'Resposta do ADN ilegível: {resposta.text[:200]}') from exc


def _sem_ns(xml):
    raiz = etree.fromstring(xml.encode('utf-8') if isinstance(xml, str) else xml)
    for el in raiz.iter():
        if isinstance(el.tag, str) and '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    return raiz


def _t(raiz, caminho):
    el = raiz.find(caminho)
    return (el.text or '').strip() if el is not None and el.text else ''


def _doc(el, caminho):
    """CNPJ ou CPF de um bloco (emit, prest, toma)."""
    return _t(el, f'{caminho}/CNPJ') or _t(el, f'{caminho}/CPF')


def ler_documento(item):
    """Um item do LoteDFe → (tipo, chave, xml)."""
    xml = gzip.decompress(base64.b64decode(item.get('ArquivoXml') or '')).decode('utf-8')
    return (item.get('TipoDocumento') or '').upper(), item.get('ChaveAcesso') or '', xml


def processar_documento(cnpj, nsu, tipo, chave, xml):
    """Grava um documento. Devolve 'nova', 'atualizada', 'evento', 'emitida' (nossa, pulada) ou ''."""
    from .models import NotaRecebida

    raiz = _sem_ns(xml)
    if tipo == 'NFSE' or raiz.tag == 'NFSe':
        inf = raiz.find('.//infNFSe')
        if inf is None:
            return ''
        dps = inf.find('.//infDPS')
        tomador = _doc(dps, 'toma') if dps is not None else ''
        prestador = _doc(inf, 'emit') or (_doc(dps, 'prest') if dps is not None else '')
        if tomador != cnpj:
            return 'emitida' if prestador == cnpj else ''
        chave = chave or (inf.get('Id') or '').replace('NFS', '')
        competencia = _t(dps, 'dCompet') if dps is not None else ''
        valor = _t(dps, 'valores/vServPrest/vServ') if dps is not None else ''
        dados = dict(
            tipo_documento='NFSE', emitente_cnpj=prestador[:14], emitente_nome=_t(inf, 'emit/xNome')[:200],
            emitente_ie=_t(inf, 'emit/IM')[:20], modelo='', serie=(_t(dps, 'serie') if dps is not None else '')[:5],
            numero=(_t(inf, 'nNFSe') or '').lstrip('0')[:15] or _t(inf, 'nNFSe')[:15],
            emissao=_data_hora((_t(dps, 'dhEmi') if dps is not None else '') or _t(inf, 'dhProc')),
            competencia=_data_hora(competencia).date() if competencia else None,
            valor=_decimal(valor or _t(inf, 'valores/vLiq')),
            valor_liquido=_decimal(_t(inf, 'valores/vLiq')) if _t(inf, 'valores/vLiq') else None,
            descricao=' '.join((_t(dps, 'serv/cServ/xDescServ') if dps is not None else '').split())[:255]
            or _t(inf, 'xTribNac')[:255],
        )
        nota, criada = NotaRecebida.objects.get_or_create(
            chave=chave[:50], defaults=dict(dados, cnpj_destinatario=cnpj, nsu=nsu, situacao='AUTORIZADA', xml_completo=xml))
        if not criada:
            for campo, valor_campo in dados.items():
                setattr(nota, campo, valor_campo)
            nota.xml_completo = xml
            nota.save()
        return 'nova' if criada else 'atualizada'

    if tipo == 'EVENTO' or raiz.tag == 'evento':
        inf = raiz.find('.//infPedReg')
        if inf is None:
            return ''
        alvo = _t(inf, 'chNFSe') or chave
        if alvo and any(inf.find(e) is not None for e in EVENTOS_DE_CANCELAMENTO):
            NotaRecebida.objects.filter(chave=alvo).update(situacao='CANCELADA', atualizada_em=timezone.now())
            return 'evento'
    return ''


def sincronizar(cnpj, transporte=None, agora=None, lotes=LOTES_POR_SINCRONIZACAO):
    """Busca as NFS-e novas do CNPJ no ADN, respeitando a espera de 1 hora depois de "nada novo"."""
    from .models import NotaRecebida, SincronizacaoDFe
    from .sefaz import vincular_lancados

    agora = agora or timezone.now()
    real = transporte is None
    transporte = transporte or _transporte_https
    sinc, _ = SincronizacaoDFe.objects.get_or_create(cnpj=cnpj)
    if sinc.proxima_consulta_nfse and sinc.proxima_consulta_nfse > agora:
        sinc.pulada_nfse = True
        return sinc
    sinc.pulada_nfse = False
    if real:
        do_certificado = cnpj_do_certificado()
        if not mesma_raiz(cnpj, do_certificado):
            sinc.ultimo_status_nfse = 'RAIZ'
            sinc.ultima_mensagem_nfse = f'O certificado é do CNPJ {receita.formatar(do_certificado)}: este CNPJ precisa do próprio.'
            sinc.ultima_consulta_nfse, sinc.notas_novas_nfse = agora, 0
            sinc.save()
            return sinc
    novas = 0
    try:
        for _ in range(lotes):
            resposta = transporte(cnpj, sinc.ult_nsu_nfse)
            status = resposta.get('StatusProcessamento') or ''
            lote = resposta.get('LoteDFe') or []
            sinc.ultimo_status_nfse = status[:40]
            erros = resposta.get('Erros') or []
            sinc.ultima_mensagem_nfse = (erros[0].get('Descricao', '') if erros and isinstance(erros[0], dict) else '')[:255]
            if status != 'DOCUMENTOS_LOCALIZADOS' or not lote:
                if status not in ('NENHUM_DOCUMENTO_LOCALIZADO', ''):
                    logger.warning('ADN %s para %s: %s', status, cnpj, sinc.ultima_mensagem_nfse)
                sinc.proxima_consulta_nfse = agora + ESPERA_SEM_NOVIDADE
                break
            with transaction.atomic():
                for item in lote:
                    try:
                        tipo, chave, xml = ler_documento(item)
                        if processar_documento(cnpj, int(item.get('NSU') or 0), tipo, chave, xml) == 'nova':
                            novas += 1
                    except Exception as exc:  # noqa: BLE001 — um documento ruim não para o lote
                        logger.warning('NFS-e NSU %s do CNPJ %s não processou: %s', item.get('NSU'), cnpj, exc)
                sinc.ult_nsu_nfse = max(int(i.get('NSU') or 0) for i in lote)
                sinc.save()
            if len(lote) < 50:
                sinc.proxima_consulta_nfse = agora + ESPERA_SEM_NOVIDADE
                break
    except SefazErro as exc:
        sinc.ultimo_status_nfse, sinc.ultima_mensagem_nfse = 'ERRO', str(exc)[:255]
        sinc.proxima_consulta_nfse = agora + timedelta(minutes=15)
    sinc.ultima_consulta_nfse = agora
    sinc.notas_novas_nfse = novas
    sinc.save()
    if novas:
        vincular_lancados(NotaRecebida.objects.filter(cnpj_destinatario=cnpj, tipo_documento='NFSE'))
    return sinc
