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
from .models import (MARCA_POSSIVEL_SERIAL, TIPO_NAO_LANCADA_NO_SAP, TIPOS_SERIAL, LinhaAuditoria,
                     SincronizacaoAuditoria, chave_de)

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
    itens = list(mysql.ler_vendas_sem_sap(desde))
    baixados = _baixados_no_estoque(itens)
    linhas = []
    for item in itens:
        venda = _texto(item.get('ID_VENDA'))
        sku = _so_letras_e_numeros(item.get('SKU'))
        seriais = {_so_letras_e_numeros(item.get('IMEI_CONSULTA')), _so_letras_e_numeros(item.get('IMEI_ORIGINAL'))} - {''}
        if (venda, 'sku', sku) in explicadas or any((venda, 'serial', s) in explicadas for s in seriais):
            continue
        if id(item) in baixados:
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


def _variantes_do_serial(item):
    """Os jeitos do serial aparecer na IQ09: o lido e, no Apple, sem o "S" do código de barras."""
    variantes = set()
    for coluna in ('IMEI_ORIGINAL', 'IMEI_CONSULTA'):
        serial = _so_letras_e_numeros(item.get(coluna))
        if len(serial) >= 8:
            variantes.add(serial)
            if serial.startswith('S') and not serial[1:].isdigit():
                variantes.add(serial[1:])
    return variantes


def _baixados_no_estoque(itens):
    """Itens que o SAP já baixou do estoque — vendidos lá, só fora da ZS103 (23/11/2026).

    "Essa venda, SC4H619717TKQ8YQA8, tá vendida certinho, porém aparece que não está
    lançada no SAP": o carregador estava no estoque livre da Anchieta (IQ09) até a carga
    de 18/11, foi vendido no Vivo GO em 19/11 e na carga seguinte não estava em loja
    nenhuma — o SAP deu a saída. Quem não trouxe foi o extrato da ZS103 (o carregador
    Apple 20W é o caso mais comum: 26 faturas no SAP para 107 vendas em novembro).
    Medido em 23/11/2026: 101 das 753 linhas da aba eram isso.

    Vale quando o serial esteve no estoque da loja da venda e não está em loja nenhuma
    na última carga da IQ09. Ainda no estoque (172) ou nunca visto (480) continua na
    lista. Se a IQ09 não responder, ninguém sai. Devolve o ``id()`` dos itens.
    """
    from .estoque import centro_da_loja

    por_item = {id(item): _variantes_do_serial(item) for item in itens}
    todos = set().union(*por_item.values()) if por_item else set()
    if not todos:
        return set()
    try:
        ultima, presenca = mysql.ler_presenca_no_estoque(todos)
    except Exception:                                 # noqa: BLE001 — sem a IQ09, a lista fica como está
        logger.warning('Auditoria SAP: estoque (IQ09) não lido para conferir as não lançadas', exc_info=True)
        return set()
    if not ultima:
        return set()
    baixados = set()
    for item in itens:
        centro = centro_da_loja(item.get('PDV'))
        onde = {}
        for serial in por_item[id(item)]:
            for c, carga in presenca.get(serial, {}).items():
                onde[c] = max(onde.get(c, ''), carga)
        if centro and centro in onde and all(carga < ultima for carga in onde.values()):
            baixados.add(id(item))
    return baixados


TIPO_NAO_ENCONTRADA_NO_VIVOGO = 'VENDA NÃO ENCONTRADA NO VIVO GO'
# Até quantos dias entre a venda no Vivo GO e a do SAP o par ainda é a mesma venda
# (medido em 18/11/2026: 259 pares no mesmo dia, 60 entre 1 e 3 dias).
DIAS_DO_PAR = 3
SERIAL_PARECIDO = 0.7


def _so_digitos(valor):
    return re.sub(r'\D', '', _texto(valor))


def _parecido(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _prova_do_par(vg, sap):
    """Por que o item do Vivo GO e a venda do SAP parecem a mesma venda: (força, motivo) ou (0, '')."""
    sku_vg, sku_sap = _so_letras_e_numeros(vg.get('SKU_VIVOGO')), _so_letras_e_numeros(sap.get('COD_MATERIAL_SAP'))
    serial_vg, serial_sap = _so_letras_e_numeros(vg.get('SERIAL_VIVOGO')), _so_letras_e_numeros(sap.get('SERIAL_SAP'))
    parecido = _parecido(serial_vg, serial_sap)
    if sku_vg and sku_vg == sku_sap:
        return 4 + parecido, 'mesmo SKU'
    if parecido >= SERIAL_PARECIDO:
        return 3 + parecido, f'serial parecido, {round(parecido * 100)}%'
    # Todo chip começa com 8955: a semelhança do ICCID não diz nada, mas chip × chip do
    # mesmo cliente no mesmo dia é o chip que saiu com o número de outro.
    if serial_vg.startswith('8955') and serial_sap.startswith('8955'):
        return 2 + parecido, 'chip × chip'
    v1, v2 = _decimal(vg.get('VALOR_VIVO_GO')), _decimal(sap.get('VALOR_SAP'))
    if v1 and v2 and abs(v1 - v2) <= max(Decimal('1'), Decimal('0.15') * max(v1, v2)):
        return 1 + parecido, 'mesmo valor'
    return 0, ''


def marcar_possiveis_erros_de_serial(brutas):
    """Marca os pares "não está no SAP" × "não está no Vivo GO" que são a mesma venda (18/11/2026).

    Mesmo CPF dos dois lados, até ``DIAS_DO_PAR`` dias de distância e uma prova: o
    mesmo SKU, o serial parecido (dígito trocado/faltando), chip × chip ou o mesmo
    valor. Cada linha entra em um par só — a mais forte primeiro —, para uma venda
    do SAP não esconder dois itens do Vivo GO. A marca vai na própria linha
    (``MARCA_POSSIVEL_SERIAL``, com o outro lado do par) e a tela deixa de mostrar.
    Devolve quantos pares.
    """
    for bruta in brutas:
        bruta.pop(MARCA_POSSIVEL_SERIAL, None)
    sap_por_cpf = {}
    for bruta in brutas:
        if _texto(bruta.get('TIPO_ERRO')) == TIPO_NAO_ENCONTRADA_NO_VIVOGO:
            cpf = _so_digitos(bruta.get('DOCUMENTO_SAP'))
            if cpf:
                sap_por_cpf.setdefault(cpf, []).append(bruta)
    candidatos = []
    for i, vg in enumerate(brutas):
        if _texto(vg.get('TIPO_ERRO')) != TIPO_NAO_LANCADA_NO_SAP:
            continue
        dia_vg = _data(vg.get('DATA_VENDA'))
        for j, sap in enumerate(sap_por_cpf.get(_so_digitos(vg.get('DOCUMENTO_VIVOGO')), ())):
            dia_sap = _data(sap.get('DATA_VENDA')) or _data(sap.get('DATA_FATURA_SAP'))
            if not (dia_vg and dia_sap) or abs((dia_vg - dia_sap).days) > DIAS_DO_PAR:
                continue
            forca, motivo = _prova_do_par(vg, sap)
            if forca:
                candidatos.append((forca, -abs((dia_vg - dia_sap).days), id(vg), id(sap), vg, sap, motivo))
    candidatos.sort(key=lambda c: (c[0], c[1]), reverse=True)
    usados, pares = set(), 0
    for _forca, _dias, id_vg, id_sap, vg, sap, motivo in candidatos:
        if id_vg in usados or id_sap in usados:
            continue
        usados.update((id_vg, id_sap))
        pares += 1
        _marcar_par(vg, sap, motivo)
    pelos_indicios = _marcar_pelos_indicios_da_query(brutas, usados)
    return pares + pelos_indicios + _marcar_seriais_quase_iguais(brutas, usados)


def _marcar_par(vg, sap, motivo):
    vg[MARCA_POSSIVEL_SERIAL] = (
        f'POSSÍVEL ERRO DE SERIAL ({motivo}) | NO SAP: {_texto(sap.get("PRODUTO_SAP"))} | '
        f'SERIAL SAP = {_texto(sap.get("SERIAL_SAP")) or "—"} | NF {_texto(sap.get("NUM_FAT_SAP")) or "—"} | '
        f'CONCILIADO NO D-1')
    sap[MARCA_POSSIVEL_SERIAL] = (
        f'POSSÍVEL ERRO DE SERIAL ({motivo}) | NO VIVO GO: VENDA {_texto(vg.get("ID_VENDA")) or "—"} | '
        f'{_texto(vg.get("PRODUTO_VIVOGO"))} | SERIAL VIVO GO = {_texto(vg.get("SERIAL_VIVOGO")) or "—"} | '
        f'CONCILIADO NO D-1')


# A própria query procura a venda no Vivo GO por CPF, SKU e data e só chama de serial
# errado quando os três batem (PONTUACAO_INDICIOS = 3). Com dois — o mesmo aparelho em
# outra cor no mesmo dia para o mesmo cliente, ou o mesmo SKU no mesmo dia com o CPF
# digitado diferente — ela manda "VENDA NÃO ENCONTRADA NO VIVO GO" (19/11/2026). É a
# mesma situação de serial errado: sai da lista também.
INDICIOS_DE_SERIAL = 2


def _pontuacao(bruta):
    try:
        return int(_texto(bruta.get('PONTUACAO_INDICIOS')) or 0)
    except ValueError:
        return 0


def _marcar_pelos_indicios_da_query(brutas, usados):
    """Linhas do SAP com 2 de 3 indícios da query; o item do Vivo GO que sobrou na mesma
    loja (até ``DIAS_DO_PAR`` dias, mesmo SKU ou serial parecido) sai junto. Devolve quantas."""
    from .permissions import normalizar_loja

    livres = {}
    for vg in brutas:
        if (_texto(vg.get('TIPO_ERRO')) == TIPO_NAO_LANCADA_NO_SAP and id(vg) not in usados
                and MARCA_POSSIVEL_SERIAL not in vg):
            livres.setdefault(normalizar_loja(vg.get('PDV')), []).append(vg)
    marcadas = 0
    for sap in brutas:
        if (_texto(sap.get('TIPO_ERRO')) != TIPO_NAO_ENCONTRADA_NO_VIVOGO or id(sap) in usados
                or MARCA_POSSIVEL_SERIAL in sap or _pontuacao(sap) < INDICIOS_DE_SERIAL):
            continue
        indicios = _texto(sap.get('INDICIOS_LOCALIZACAO')) or f'{_pontuacao(sap)} de 3 indícios'
        motivo = f'a query achou a venda no Vivo GO por 2 de 3 indícios: {indicios}'
        dia_sap = _data(sap.get('DATA_VENDA')) or _data(sap.get('DATA_FATURA_SAP'))
        sku_sap = _so_letras_e_numeros(sap.get('COD_MATERIAL_SAP'))
        serial_sap = _so_letras_e_numeros(sap.get('SERIAL_SAP'))
        melhor, nota = None, 0.0
        for vg in livres.get(normalizar_loja(sap.get('PDV')), ()):
            if id(vg) in usados:
                continue
            dia_vg = _data(vg.get('DATA_VENDA'))
            if not (dia_vg and dia_sap) or abs((dia_vg - dia_sap).days) > DIAS_DO_PAR:
                continue
            parecido = _parecido(_so_letras_e_numeros(vg.get('SERIAL_VIVOGO')), serial_sap)
            mesmo_sku = bool(sku_sap) and _so_letras_e_numeros(vg.get('SKU_VIVOGO')) == sku_sap
            if (mesmo_sku or parecido >= SERIAL_PARECIDO) and parecido + mesmo_sku > nota:
                melhor, nota = vg, parecido + mesmo_sku
        usados.add(id(sap))
        marcadas += 1
        if melhor is not None:
            usados.add(id(melhor))
            _marcar_par(melhor, sap, motivo)
        else:
            sap[MARCA_POSSIVEL_SERIAL] = f'POSSÍVEL ERRO DE SERIAL ({motivo}) | CONCILIADO NO D-1'
    return marcadas


# Serial incorreto (23/11/2026): "está interpretando como não lançado no Vivo GO, uma aba
# sai e corrige a outra". A loja bipa no Vivo GO o chip vizinho da cartela (ou digita um
# IMEI com um dígito trocado): o SAP fica com uma venda "não encontrada no Vivo GO" e o
# Vivo GO com um item "não lançado no SAP" — a mesma unidade, duas linhas, e corrigir o
# serial apaga as duas. O CPF não ajuda (no chip trocado é muitas vezes o de outra venda),
# então a prova é o próprio serial: mesma loja, até ``DIAS_DO_PAR`` dias, até
# ``DIGITOS_DO_SERIAL`` dígitos de diferença e o mesmo tipo de peça (chip × chip ou
# aparelho/acessório × aparelho/acessório). Medido em 23/11/2026: ~615 linhas da aba
# "Lançado no SAP e não no Vivo GO" com o par a 1–2 dígitos na mesma loja.
DIGITOS_DO_SERIAL = 2
SERIAL_MINIMO = 12          # serial curto demais (código interno, "1") não prova nada


def _distancia_ate(a, b, limite):
    """Distância de edição entre ``a`` e ``b``, ou ``limite + 1`` se passar do limite."""
    if abs(len(a) - len(b)) > limite:
        return limite + 1
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        atual = [i] + [0] * len(b)
        menor = atual[0]
        for j, cb in enumerate(b, 1):
            atual[j] = min(anterior[j] + 1, atual[j - 1] + 1, anterior[j - 1] + (ca != cb))
            menor = min(menor, atual[j])
        if menor > limite:
            return limite + 1
        anterior = atual
    return min(anterior[-1], limite + 1)


def _marcar_seriais_quase_iguais(brutas, usados):
    """Pares que sobraram com o serial a até ``DIGITOS_DO_SERIAL`` dígitos — um para um,
    o mais parecido (e mais perto no tempo) primeiro. Devolve quantos."""
    from .permissions import normalizar_loja

    vg_por_loja = {}
    for vg in brutas:
        if (_texto(vg.get('TIPO_ERRO')) == TIPO_NAO_LANCADA_NO_SAP and id(vg) not in usados
                and MARCA_POSSIVEL_SERIAL not in vg):
            serial = _so_letras_e_numeros(vg.get('SERIAL_VIVOGO'))
            if len(serial) >= SERIAL_MINIMO:
                vg_por_loja.setdefault(normalizar_loja(vg.get('PDV')), []).append((serial, vg))
    candidatos = []
    for sap in brutas:
        if (_texto(sap.get('TIPO_ERRO')) != TIPO_NAO_ENCONTRADA_NO_VIVOGO or id(sap) in usados
                or MARCA_POSSIVEL_SERIAL in sap):
            continue
        serial_sap = _so_letras_e_numeros(sap.get('SERIAL_SAP'))
        dia_sap = _data(sap.get('DATA_VENDA')) or _data(sap.get('DATA_FATURA_SAP'))
        if len(serial_sap) < SERIAL_MINIMO or not dia_sap:
            continue
        chip_sap = serial_sap.startswith('8955')
        for serial_vg, vg in vg_por_loja.get(normalizar_loja(sap.get('PDV')), ()):
            dia_vg = _data(vg.get('DATA_VENDA'))
            if not dia_vg or abs((dia_vg - dia_sap).days) > DIAS_DO_PAR:
                continue
            if serial_vg.startswith('8955') != chip_sap:
                continue
            distancia = _distancia_ate(serial_sap, serial_vg, DIGITOS_DO_SERIAL)
            if 0 < distancia <= DIGITOS_DO_SERIAL:
                candidatos.append((distancia, abs((dia_vg - dia_sap).days), id(vg), id(sap), vg, sap))
    candidatos.sort(key=lambda c: (c[0], c[1]))
    pares = 0
    for distancia, _dias, id_vg, id_sap, vg, sap in candidatos:
        if id_vg in usados or id_sap in usados:
            continue
        usados.update((id_vg, id_sap))
        pares += 1
        _marcar_par(vg, sap, f'serial incorreto: {distancia} dígito{"s" if distancia > 1 else ""} '
                             f'de diferença na mesma loja')
    return pares


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
    try:
        marcar_possiveis_erros_de_serial(brutas)
    except Exception:                                 # noqa: BLE001 — sem a marca, a linha só aparece na tela
        logger.warning('Auditoria SAP: possíveis erros de serial não marcados agora', exc_info=True)

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
        if (not leu_nao_lancadas and linha is not None and MARCA_POSSIVEL_SERIAL in (linha.dados or {})
                and MARCA_POSSIVEL_SERIAL not in valores['dados']):
            # Sem o lado do Vivo GO nesta leitura não dá para refazer o par: fica o de antes.
            valores['dados'][MARCA_POSSIVEL_SERIAL] = linha.dados[MARCA_POSSIVEL_SERIAL]
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
