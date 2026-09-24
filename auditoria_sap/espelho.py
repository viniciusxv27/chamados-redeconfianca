"""Traz a auditoria do SAP para o espelho local.

Ler a view inteira leva ~30 s, então isso não roda a cada tela: roda quando
alguém manda atualizar (ou pelo comando ``sincronizar_sap``, se algum dia virar
rotina). A tela trabalha sempre em cima do espelho.

O que é do SAP o espelho sobrescreve; o que é do portal (resolvida, quem
marcou, observação) nunca é tocado aqui.
"""
import logging
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from . import mysql
from .models import LinhaAuditoria, SincronizacaoAuditoria, chave_de

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
               if chave not in vistas and linha.ativa]
    for linha in sumiram:
        linha.ativa = False
        linha.sumiu_em = agora
    if sumiram:
        LinhaAuditoria.objects.bulk_update(sumiram, ['ativa', 'sumiu_em'], batch_size=500)

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


def ultima_sincronizacao():
    return SincronizacaoAuditoria.objects.first()
