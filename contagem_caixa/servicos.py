"""Importação da base de vendas e recálculo do saldo acumulado."""
import logging
import re
import unicodedata
from decimal import Decimal

from django.utils import timezone

from .models import ConfiguracaoContagem, ContagemCaixaDia
from .permissions import lojas

logger = logging.getLogger(__name__)
ZERO = Decimal('0.00')


def _chave(texto):
    """Nome comparável: sem acento, sem pontuação, espaços colapsados, caixa alta."""
    sem_acento = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', re.sub(r'[^A-Za-z0-9 ]', ' ', sem_acento)).strip().upper()


def _indice_setores():
    """Índice de lojas por código ADABAS e por nome normalizado.

    O código (``CD_CRDN`` = 'ESD0267-004') casa direto com o ADABAS do setor e
    é o caminho confiável. O nome fica como reserva: a planilha traz
    'EA MASTERCEL SERRA II ES' e o portal tem 'Loja Serra Sede'. O que não
    casar por nenhum dos dois é reportado em vez de sumir calado.
    """
    por_codigo, por_nome = {}, {}
    for setor in lojas():
        if setor.adabas:
            por_codigo[_chave(setor.adabas)] = setor
        nome = _chave(setor.name)
        por_nome.setdefault(nome, setor)
        por_nome.setdefault(re.sub(r'^LOJA ', '', nome).strip(), setor)
    return {'codigo': por_codigo, 'nome': por_nome}


def casar_loja(codigo, nome_planilha, indice=None):
    """Encontra o setor da linha pelo código ADABAS e, na falta, pelo nome."""
    indice = indice if indice is not None else _indice_setores()

    chave_codigo = _chave(codigo)
    if chave_codigo and chave_codigo in indice['codigo']:
        return indice['codigo'][chave_codigo]

    alvo = _chave(nome_planilha)
    if not alvo:
        return None
    if alvo in indice['nome']:
        return indice['nome'][alvo]

    # Última tentativa: o nome do setor aparece dentro do nome da planilha.
    melhores = [(len(k), s) for k, s in indice['nome'].items()
                if k and len(k) >= 4 and k in alvo]
    return max(melhores)[1] if melhores else None


def _valor_do_texto(texto):
    """Extrai 'R$ 1.234,56' de 'DINHEIRO - R$ 1.234,56' e devolve float."""
    achado = re.search(r'R\$\s*([\d.]+,\d{2})', str(texto))
    if not achado:
        return 0.0
    return float(achado.group(1).replace('.', '').replace(',', '.'))


def _abrir(arquivo, config):
    import pandas as pd
    try:
        return pd.read_excel(arquivo, sheet_name=config.aba, engine='openpyxl')
    except ValueError:
        # Aba com outro nome: cai para a primeira.
        if hasattr(arquivo, 'seek'):
            arquivo.seek(0)
        return pd.read_excel(arquivo, sheet_name=0, engine='openpyxl')


def _exigir(df, colunas):
    faltando = [c for c in colunas if c and c not in df.columns]
    if faltando:
        raise ValueError(
            f"A planilha não tem a(s) coluna(s): {', '.join(faltando)}. "
            f"Colunas encontradas: {', '.join(map(str, df.columns[:12]))}…")


def ler_planilha(arquivo, config=None):
    """Devolve [(codigo, nome_loja, data, valor)] já somado por loja/dia."""
    import pandas as pd

    config = config or ConfiguracaoContagem.get()
    df = _abrir(arquivo, config)
    _exigir(df, [config.coluna_loja, config.coluna_data])

    if config.filtro_coluna and config.filtro_coluna in df.columns:
        alvo = config.filtro_valor.strip().upper()
        coluna = df[config.filtro_coluna].astype(str).str.strip().str.upper()
        df = df[coluna == alvo] if alvo else df[coluna.notna()]

    codigo = config.coluna_codigo if config.coluna_codigo in df.columns else None
    df = df.dropna(subset=[config.coluna_loja, config.coluna_data]).copy()
    df['_data'] = pd.to_datetime(df[config.coluna_data], errors='coerce')
    df = df.dropna(subset=['_data'])
    df['_codigo'] = df[codigo].astype(str) if codigo else ''
    df['_loja'] = df[config.coluna_loja].astype(str)

    if config.modo == ConfiguracaoContagem.Modo.FORMA_PGTO:
        df['_valor'] = _somar_forma_pagamento(df, config)
    else:
        _exigir(df, [config.coluna_valor])
        df['_valor'] = pd.to_numeric(df[config.coluna_valor], errors='coerce').fillna(0.0)

    agrupado = (df.groupby(['_codigo', '_loja', df['_data'].dt.date])['_valor']
                  .sum().reset_index())
    agrupado = agrupado[agrupado['_valor'] != 0]
    return [(l['_codigo'], l['_loja'], l['_data'], Decimal(str(round(float(l['_valor']), 2))))
            for _, l in agrupado.iterrows()]


def _somar_forma_pagamento(df, config):
    """Valor de uma forma de pagamento por linha, sem contar o pedido duas vezes.

    A condição de pagamento ('DINHEIRO - R$ 258,00') se repete em toda linha de
    produto do mesmo pedido. Somar direto multiplicaria o caixa pelo número de
    itens vendidos, então cada par (pedido, condição) só conta uma vez — e o
    valor fica na primeira linha do pedido.
    """
    import pandas as pd

    colunas = [c.strip() for c in (config.colunas_condicao or '').split(',') if c.strip()]
    colunas = [c for c in colunas if c in df.columns]
    if not colunas:
        raise ValueError(
            'Nenhuma coluna de condição de pagamento foi encontrada na planilha '
            f"(procurei por: {config.colunas_condicao}).")

    pedido = (config.coluna_pedido if config.coluna_pedido in df.columns else None)
    alvo = _chave(config.forma_pagamento) or 'DINHEIRO'

    total = pd.Series(0.0, index=df.index)
    for coluna in colunas:
        texto = df[coluna].astype(str)
        e_da_forma = texto.map(lambda t: _chave(t).startswith(alvo))
        valores = texto.where(e_da_forma).map(_valor_do_texto).fillna(0.0)
        if pedido is not None:
            # Mantém só a primeira ocorrência de cada (pedido, condição).
            repetido = pd.DataFrame({'p': df[pedido].astype(str), 'c': texto}).duplicated()
            valores = valores.where(~repetido, 0.0)
        total = total + valores
    return total


def previa(arquivo, config=None):
    """O que a importação faria, sem gravar nada.

    Serve para conferir o recorte antes de mexer no controle de caixa: mostra
    total por loja, o período e quais lojas não têm setor no portal.
    """
    linhas = ler_planilha(arquivo, config)
    indice = _indice_setores()

    por_loja, sem_setor = {}, set()
    for codigo, nome, data, valor in linhas:
        setor = casar_loja(codigo, nome, indice)
        if not setor:
            sem_setor.add(f'{nome} ({codigo})' if codigo else str(nome))
        chave = setor.name if setor else f'⚠ {nome}'
        item = por_loja.setdefault(chave, {'dias': 0, 'total': ZERO, 'setor': setor})
        item['dias'] += 1
        item['total'] += valor

    datas = [d for _, _, d, _ in linhas]
    return {
        'linhas': len(linhas),
        'por_loja': sorted(por_loja.items()),
        'sem_setor': sorted(sem_setor),
        'inicio': min(datas) if datas else None,
        'fim': max(datas) if datas else None,
        'total': sum((v for _, _, _, v in linhas), ZERO),
    }


def importar(arquivo, usuario=None, config=None):
    """Grava o Valor SAP de cada loja/dia e recalcula os saldos afetados."""
    config = config or ConfiguracaoContagem.get()
    linhas = ler_planilha(arquivo, config)
    indice = _indice_setores()
    agora = timezone.now()

    criados = atualizados = 0
    sem_setor = set()
    afetadas = {}

    for codigo, nome, data, valor in linhas:
        setor = casar_loja(codigo, nome, indice)
        if not setor:
            sem_setor.add(f'{nome} ({codigo})' if codigo else str(nome))
            continue
        dia, novo = ContagemCaixaDia.objects.get_or_create(
            loja=setor, data=data,
            defaults={'valor_sap': valor, 'importado_em': agora})
        if not novo:
            # A importação manda no Valor SAP; o que foi preenchido na tela
            # (vivogo, sangria, valor real…) não é tocado.
            dia.valor_sap = valor
            dia.importado_em = agora
            dia.save(update_fields=['valor_sap', 'importado_em', 'atualizado_em'])
            atualizados += 1
        else:
            criados += 1
        anterior = afetadas.get(setor.id)
        afetadas[setor.id] = min(anterior, data) if anterior else data

    for setor_id, desde in afetadas.items():
        recalcular_saldos(setor_id, desde)

    return {
        'linhas': len(linhas),
        'criados': criados,
        'atualizados': atualizados,
        'sem_setor': sorted(sem_setor),
        'lojas': len(afetadas),
    }


def recalcular_saldos(loja_id, desde=None):
    """Refaz o saldo acumulado da loja a partir de uma data.

    O saldo é encadeado (saldo do dia = saldo anterior + valor real), então
    mexer num dia do meio obriga a refazer todos os seguintes.
    """
    qs = ContagemCaixaDia.objects.filter(loja_id=loja_id).order_by('data')
    if desde:
        anterior = (qs.filter(data__lt=desde).order_by('-data').first())
        saldo = anterior.saldo if anterior else ZERO
        qs = qs.filter(data__gte=desde)
    else:
        saldo = ZERO

    alterados = []
    for dia in qs:
        saldo = dia.calcular_saldo(saldo)
        if dia.saldo != saldo:
            dia.saldo = saldo
            alterados.append(dia)
    if alterados:
        ContagemCaixaDia.objects.bulk_update(alterados, ['saldo'], batch_size=500)
    return len(alterados)


def gerentes_da_loja(setor):
    """Quem deve ser avisado de uma divergência na loja.

    O setor não guarda quem é o gestor, então a busca é por quem está lotado
    na loja com cargo de gerente ('GERENTE DE VENDAS', 'GERENTE OPERACIONAL'…).
    Se a loja não tiver gerente cadastrado, cai para a supervisão dela — o
    alerta não pode simplesmente se perder.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    User = get_user_model()

    da_loja = User.objects.filter(
        Q(sector=setor) | Q(sectors=setor), is_active=True).distinct()

    pessoas = list(da_loja.filter(job_title__icontains='GERENTE'))
    if not pessoas:
        pessoas = list(da_loja.filter(
            Q(job_title__icontains='COORDENADOR') | Q(job_title__icontains='SUPERVISOR')
            | Q(hierarchy__in=['SUPERVISOR', 'ADMIN', 'SUPERADMIN'])))
    return pessoas


def notificar_atencao(dia):
    """Avisa o gerente da loja quando o dia fica em Atenção.

    Só avisa uma vez por dia de caixa: o gerente não precisa receber o mesmo
    alerta a cada vez que alguém salva a tela.
    """
    from core.models import NotificationMixin

    config = ConfiguracaoContagem.get()
    if not config.notificar_gerente or not dia.em_atencao or dia.notificado_em:
        return []

    pessoas = gerentes_da_loja(dia.loja)
    if not pessoas:
        logger.warning('Contagem de caixa: %s em atenção, mas a loja não tem gerente.',
                       dia.loja.name)
        return []

    try:
        NotificationMixin.create_notifications_for_users(
            users=pessoas,
            title=f'Divergência de caixa — {dia.loja.name}',
            message=(f'{dia.data:%d/%m/%Y}: SAP R$ {dia.valor_sap} x Vivo go R$ '
                     f'{dia.valor_vivogo} — divergência de R$ {dia.divergencia}.'),
            notification_type='SYSTEM',
            related_url=f'/contagem-caixa/loja/{dia.loja_id}/',
        )
    except Exception as exc:
        logger.warning('Falha ao notificar divergência de caixa: %s', exc)
        return []

    dia.notificado_em = timezone.now()
    dia.save(update_fields=['notificado_em'])
    return pessoas
