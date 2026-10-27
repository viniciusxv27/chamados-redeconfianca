"""Planos fora do portfólio: o que o cliente tem e a Parametrização não vende (27/10/2026).

A lista vem do histórico do VivoGo (``vendas_servicos``): o plano novo e o plano antigo de
cada venda, com o valor. Nome que já existe como plano normal — de qualquer segmentação —
não entra: aí o vendedor escolhe o plano normal.
"""
import unicodedata
from collections import Counter, defaultdict
from datetime import date

# Tipo_do_plano / Grupo_do_tipo_de_plano_antigo do VivoGo → segmentação do portal. "SVA" fica de fora.
SEGMENTACAO_DO_TIPO = {
    'controle': 'CONTROLE', 'pos': 'POS', 'pre': 'PRE',
    'fixa massiva + premium': 'FIBRA', 'vivo empresas': 'EMPRESAS',
}

SQL = """
    SELECT nome, valor, tipo, COUNT(*) AS vendas, MAX(dia) AS ultima FROM (
        SELECT `Plano_novo` AS nome, `Valor_do_plano_novo` AS valor, `Tipo_do_plano` AS tipo, data_da_venda AS dia
        FROM vendas_servicos
        WHERE data_da_venda <= %s AND `Tipo_do_plano` IN ('Controle', 'Pós', 'Pré', 'Fixa Massiva + Premium', 'Vivo Empresas')
        UNION ALL
        SELECT `Plano_antigo`, `Valor_do_plano_antigo`, `Grupo_do_tipo_de_plano_antigo`, data_da_venda
        FROM vendas_servicos
        WHERE data_da_venda <= %s AND COALESCE(`Plano_antigo`, '') <> ''
    ) x
    WHERE COALESCE(nome, '') <> '' AND valor IS NOT NULL
    GROUP BY nome, valor, tipo
"""


def chave(nome):
    """Para comparar nomes: sem acento, sem caixa e com espaços simples."""
    texto = unicodedata.normalize('NFKD', str(nome or ''))
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return ' '.join(texto.lower().split())


def _consultar(ate):
    import pymysql
    from simulator.sql_realizado import _mysql_config

    # Agrupar 276 mil vendas leva mais que o limite curto das telas de consulta.
    conexao = pymysql.connect(**dict(_mysql_config(), connect_timeout=30, read_timeout=600,
                                     cursorclass=pymysql.cursors.DictCursor))
    try:
        with conexao.cursor() as cur:
            cur.execute(SQL, (ate, ate))
            return cur.fetchall()
    finally:
        conexao.close()


def agrupar(linhas):
    """Uma entrada por nome+valor (o mesmo plano escrito em caixas diferentes vira um só)."""
    grupos = defaultdict(lambda: {'grafias': Counter(), 'tipos': Counter(), 'vendas': 0, 'ultima': None})
    for l in linhas:
        nome = ' '.join(str(l['nome']).split())[:200]
        g = grupos[(chave(nome), l['valor'])]
        g['grafias'][nome] += l['vendas']
        seg = SEGMENTACAO_DO_TIPO.get(chave(l['tipo']))
        if seg:
            g['tipos'][seg] += l['vendas']
        g['vendas'] += l['vendas']
        if l['ultima'] and (g['ultima'] is None or l['ultima'] > g['ultima']):
            g['ultima'] = l['ultima']
    return [{'nome': g['grafias'].most_common(1)[0][0], 'valor': valor,
             'segmentacao': g['tipos'].most_common(1)[0][0] if g['tipos'] else '',
             'vendas': g['vendas'], 'ultima': g['ultima']}
            for (_c, valor), g in grupos.items()]


def importar_do_vivogo(ate=date(2026, 10, 27), linhas=None):
    """Sobe os planos do VivoGo que não existem como plano normal. Devolve a contagem.

    Rodar de novo é seguro: o que já está na lista só tem as vendas e a última venda atualizadas.
    """
    from .models import Plano, PlanoForaPortfolio

    linhas = _consultar(ate) if linhas is None else linhas
    normais = {chave(n) for n in Plano.objects.values_list('nome', flat=True)}
    existentes = {(chave(p.nome), p.valor): p for p in PlanoForaPortfolio.objects.all()}
    novos, atualizados, ja_normais = [], [], 0
    for item in agrupar(linhas):
        if chave(item['nome']) in normais:
            ja_normais += 1
            continue
        atual = existentes.get((chave(item['nome']), item['valor']))
        if atual:
            atual.vendas, atual.ultima_venda = item['vendas'], item['ultima']
            atualizados.append(atual)
            continue
        novos.append(PlanoForaPortfolio(nome=item['nome'], valor=item['valor'], segmentacao=item['segmentacao'],
                                        vendas=item['vendas'], ultima_venda=item['ultima']))
    PlanoForaPortfolio.objects.bulk_create(novos, ignore_conflicts=True)
    PlanoForaPortfolio.objects.bulk_update(atualizados, ['vendas', 'ultima_venda'])
    return {'lidos': len(linhas), 'novos': len(novos), 'atualizados': len(atualizados), 'ja_normais': ja_normais}
