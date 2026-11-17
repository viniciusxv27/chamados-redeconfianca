"""Traz a auditoria do SAP para o espelho local.

Ler a view inteira leva ~30 s, então isso não roda a cada tela: roda quando
alguém manda atualizar (ou pelo comando ``sincronizar_sap``, se algum dia virar
rotina). A tela trabalha sempre em cima do espelho.

O que é do SAP o espelho sobrescreve; o que é do portal (resolvida, quem
marcou, observação) nunca é tocado aqui.
"""
import logging
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from . import mysql
from .models import (TIPO_NAO_LANCADA_NO_SAP, TIPOS_SERIAL, LinhaAuditoria, SincronizacaoAuditoria,
                     chave_de)

logger = logging.getLogger(__name__)

CENTAVO = Decimal('0.01')


def _texto(valor, limite=None):
    if valor is None:
        return ''
    texto = str(valor).strip()
    return texto[:limite] if limite else texto


def _decimal(valor):
    if valor is None or valor == '':
        return None
    try:
        return Decimal(str(valor)).quantize(CENTAVO)
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _inteiro(valor):
    if valor is None or valor == '':
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _data(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = _texto(valor)[:10]
    for formato in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None

# campo do espelho ← coluna da view (o resto da linha vai inteiro em `dados`)
CAMPOS = (
    ('tipo_erro', 'TIPO_ERRO', lambda v: _texto(v, 60)),
    ('data_venda', 'DATA_VENDA', _data),
    ('pdv', 'PDV', lambda v: _texto(v, 120)),
    ('loja_vivogo', 'LOJA_VIVOGO', lambda v: _texto(v, 120)),
    ('id_venda', 'ID_VENDA', lambda v: _texto(v, 60)),
    ('nome_cliente', 'NOME_CLIENTE', lambda v: _texto(v, 180)),
    ('documento_vivogo', 'DOCUMENTO_VIVOGO', lambda v: _texto(v, 30)),
    ('produto_vivogo', 'PRODUTO_VIVOGO', lambda v: _texto(v, 255)),
    ('produto_sap', 'PRODUTO_SAP', lambda v: _texto(v, 255)),
    ('sku_vivogo', 'SKU_VIVOGO', lambda v: _texto(v, 60)),
    ('serial_vivogo', 'SERIAL_VIVOGO', lambda v: _texto(v, 300)),
    ('serial_sap', 'SERIAL_SAP', _texto),
    ('ordem_sap', 'ORDEM_SAP', lambda v: _texto(v, 30)),
    ('num_fat_sap', 'NUM_FAT_SAP', lambda v: _texto(v, 40)),
    ('status_nf', 'STATUS_NF', lambda v: _texto(v, 60)),
    ('valor_sap', 'VALOR_SAP', _decimal),
    ('valor_vivogo', 'VALOR_VIVO_GO', _decimal),
    ('diferenca_valor', 'DIFERENCA_VALOR', _decimal),
    ('situacao_valor', 'SITUACAO_VALOR', lambda v: _texto(v, 180)),
    ('status_documento', 'STATUS_DOCUMENTO', lambda v: _texto(v, 40)),
    ('status_produto', 'STATUS_PRODUTO', lambda v: _texto(v, 40)),
    ('status_valor', 'STATUS_VALOR', lambda v: _texto(v, 40)),
    ('status_loja', 'STATUS_LOJA', lambda v: _texto(v, 40)),
    ('status_data', 'STATUS_DATA', lambda v: _texto(v, 40)),
    ('diferenca_dias', 'DIFERENCA_DIAS', _inteiro),
    ('diferenca_explicita', 'DIFERENCA_EXPLICITA', _texto),
    ('pontuacao_indicios', 'PONTUACAO_INDICIOS', _inteiro),
)

CAMPOS_DO_SAP = [campo for campo, _, _ in CAMPOS] + ['dados']


def _legivel(valor):
    """Como o valor aparece na ficha da linha."""
    if valor is None:
        return ''
    if isinstance(valor, datetime):
        return valor.strftime('%d/%m/%Y %H:%M')
    if isinstance(valor, date):
        return valor.strftime('%d/%m/%Y')
    if isinstance(valor, Decimal):
        return f'{valor:.2f}'
    return str(valor)


def valores_da_linha(bruta):
    """Os campos do espelho a partir de uma linha da view."""
    valores = {campo: conversor(bruta.get(coluna)) for campo, coluna, conversor in CAMPOS}
    # A linha inteira, como texto, para a ficha mostrar até o que não virou
    # coluna aqui — a view pode ganhar campo novo sem a tela ficar cega.
    valores['dados'] = {coluna: _legivel(valor) for coluna, valor in bruta.items()}
    return valores


def _so_letras_e_numeros(valor):
    return re.sub(r'[^0-9A-Z]', '', _texto(valor).upper())


def linhas_nao_lancadas_no_sap(brutas):
    """Itens lançados no Vivo GO e não no SAP, no formato das linhas da view (17/11/2026).

    A vw_auditoria_visao_geral só olha do SAP para o Vivo GO; o lado de lá vem da
    vw_conferencia_vendas_produtos (``mysql.ler_vendas_sem_sap``), no mesmo período
    da auditoria (a venda mais antiga que ela trouxe).

    Fica de fora o item que a auditoria já explica como serial errado/ausente: o
    serial no Vivo GO está errado, então ele "não acha" o SAP, mas a venda está lá
    (casada por CPF + SKU + data). A ponte é a venda do Vivo GO (ID_VENDA) com o
    mesmo SKU ou o mesmo serial — medido em 17/11/2026: 382 de 1.546 itens.
    """
    datas = [d for d in (_data(b.get('DATA_VENDA')) for b in brutas) if d]
    desde = min(datas) if datas else timezone.localdate() - timedelta(days=90)
    explicadas = set()
    for bruta in brutas:
        if _texto(bruta.get('TIPO_ERRO')) not in TIPOS_SERIAL:
            continue
        venda = _texto(bruta.get('ID_VENDA'))
        for campo, coluna in (('sku', 'SKU_VIVOGO'), ('serial', 'SERIAL_VIVOGO')):
            valor = _so_letras_e_numeros(bruta.get(coluna))
            if venda and valor:
                explicadas.add((venda, campo, valor))
    linhas = []
    for item in mysql.ler_vendas_sem_sap(desde):
        venda = _texto(item.get('ID_VENDA'))
        sku = _so_letras_e_numeros(item.get('SKU'))
        seriais = {_so_letras_e_numeros(item.get('IMEI_CONSULTA')), _so_letras_e_numeros(item.get('IMEI_ORIGINAL'))} - {''}
        if (venda, 'sku', sku) in explicadas or any((venda, 'serial', s) in explicadas for s in seriais):
            continue
        valor = _decimal(item.get('RECEITA_VIVOGO'))
        serial = _texto(item.get('IMEI_ORIGINAL')) or _texto(item.get('IMEI_CONSULTA'))
        pdv = _texto(item.get('PDV'))
        linhas.append({
            'TIPO_ERRO': TIPO_NAO_LANCADA_NO_SAP,
            'DATA_VENDA': item.get('DATA_VENDA'),
            'PDV': pdv,
            'LOJA_VIVOGO': pdv,
            'ID_VENDA': venda,
            'NOME_CLIENTE': item.get('NOME_CLIENTE'),
            'DOCUMENTO_VIVOGO': item.get('CPF_CLIENTE_VIVOGO'),
            'PRODUTO_VIVOGO': item.get('NOME_COMERCIAL_PRODUTO'),
            'TIPO_PRODUTO': item.get('TIPO_PRODUTO'),
            'SKU_VIVOGO': item.get('SKU'),
            'SERIAL_VIVOGO': serial,
            'VALOR_VIVO_GO': valor,
            # Mesmo sinal da view (SAP − Vivo GO): o SAP não tem nada desse item.
            'DIFERENCA_VALOR': -valor if valor else None,
            'SITUACAO_VALOR': 'VENDA NÃO ENCONTRADA NO SAP',
            'DIFERENCA_EXPLICITA': (f'LANÇADA NO VIVO GO E NÃO NO SAP | SERIAL VIVO GO = {serial or "—"} '
                                    f'NÃO ESTÁ NA ZS103'),
            'QTD_VENDIDA': item.get('QTD_VENDIDA'),
            'VENDEDOR': item.get('VENDEDOR'),
            'ORIGEM': 'vw_conferencia_vendas_produtos',
        })
    return linhas


def _mudou(linha, valores):
    for campo, valor in valores.items():
        if getattr(linha, campo) != valor:
            return True
    return False


def sincronizar(por=None):
    """Lê o SAP e atualiza o espelho. Devolve o resumo do que mudou."""
    comeco = time.monotonic()
    try:
        brutas = mysql.ler_visao_geral()
    except mysql.SapIndisponivel as exc:
        SincronizacaoAuditoria.objects.create(
            por=por if getattr(por, 'pk', None) else None,
            erro=str(exc)[:2000], segundos=round(time.monotonic() - comeco, 1))
        raise

    # Lançadas no Vivo GO e não no SAP: se essa leitura falhar, a auditoria segue e
    # as linhas desse tipo que já estão no espelho ficam como estão (não "saem").
    leu_nao_lancadas = True
    try:
        brutas = list(brutas) + linhas_nao_lancadas_no_sap(brutas)
    except Exception:                                 # noqa: BLE001 — a auditoria já foi lida
        leu_nao_lancadas = False
        logger.warning('Auditoria SAP: vendas do Vivo GO sem SAP não lidas agora', exc_info=True)

    agora = timezone.now()
    existentes = {linha.chave: linha for linha in LinhaAuditoria.objects.all()}
    vistas = set()
    novas, alteradas = [], []

    for bruta in brutas:
        chave = chave_de(bruta)
        if chave in vistas:
            # Linha repetida byte a byte: é a mesma divergência contada duas
            # vezes pela view; uma linha no espelho basta.
            continue
        vistas.add(chave)
        valores = valores_da_linha(bruta)
        linha = existentes.get(chave)
        if linha is None:
            novas.append(LinhaAuditoria(chave=chave, visto_em=agora, **valores))
            continue
        precisa = _mudou(linha, valores) or not linha.ativa
        for campo, valor in valores.items():
            setattr(linha, campo, valor)
        linha.visto_em = agora
        if not linha.ativa:
            linha.ativa = True
            linha.sumiu_em = None
        if precisa:
            alteradas.append(linha)

    if novas:
        LinhaAuditoria.objects.bulk_create(novas, batch_size=500)
    if alteradas:
        LinhaAuditoria.objects.bulk_update(
            alteradas, CAMPOS_DO_SAP + ['ativa', 'visto_em', 'sumiu_em'], batch_size=500)

    sumiram = [linha for chave, linha in existentes.items()
               if chave not in vistas and linha.ativa
               and (leu_nao_lancadas or linha.tipo_erro != TIPO_NAO_LANCADA_NO_SAP)]
    for linha in sumiram:
        linha.ativa = False
        linha.sumiu_em = agora
    if sumiram:
        LinhaAuditoria.objects.bulk_update(sumiram, ['ativa', 'sumiu_em'], batch_size=500)

    try:
        preencher_formas_de_pagamento()
    except mysql.SapIndisponivel:                     # a auditoria já foi lida; o pagamento vem na próxima
        logger.warning('Auditoria SAP: formas de pagamento não lidas agora', exc_info=True)

    try:
        # Visão Renova: as ordens ZVNB vêm na mesma leitura (auditoria_sap/renova.py).
        from .renova import ler_do_sap
        ler_do_sap()
    except Exception:                                 # noqa: BLE001 — a auditoria já foi lida
        logger.warning('Visão Renova não atualizada agora', exc_info=True)

    resumo = {
        'novas': len(novas),
        'atualizadas': len(alteradas),
        'sumiram': len(sumiram),
        'total': len(vistas),
        'segundos': round(time.monotonic() - comeco, 1),
    }
    SincronizacaoAuditoria.objects.create(
        por=por if getattr(por, 'pk', None) else None, **resumo)
    logger.info('Auditoria SAP sincronizada: %s', resumo)
    return resumo


def preencher_formas_de_pagamento(formas=None):
    """Põe em cada linha a forma de pagamento da nota (SAP.zs103_final). Devolve quantas mudaram."""
    formas = mysql.ler_formas_de_pagamento() if formas is None else formas
    mudaram = []
    for linha in LinhaAuditoria.objects.exclude(num_fat_sap='').only('id', 'num_fat_sap', 'forma_pagamento'):
        forma = formas.get(linha.num_fat_sap.strip(), '')[:80]
        if forma and forma != linha.forma_pagamento:
            linha.forma_pagamento = forma
            mudaram.append(linha)
    LinhaAuditoria.objects.bulk_update(mudaram, ['forma_pagamento'], batch_size=500)
    return len(mudaram)


def ultima_sincronizacao():
    return SincronizacaoAuditoria.objects.first()
