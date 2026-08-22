"""Histórico de compras do cliente, lido do MySQL do Vivo GO.

Serve para o vendedor saber, no balcão, se quem está na frente dele já é
cliente da rede — e o que ele já levou.

Duas coisas moldam o desenho:

* **Não há índice de CPF** nas tabelas (143 mil produtos, 251 mil serviços), e
  cada consulta varre ~1,8 s por tabela. Por isso a busca só dispara com o CPF
  completo e o resultado fica em cache — digitar dígito a dígito derrubaria o
  banco de vendas da empresa.
* **O MySQL é de outro sistema e pode estar fora.** Qualquer falha devolve
  ``disponivel: False`` e o formulário segue funcionando: conferir histórico é
  um bônus, não pode impedir de lançar a venda.
"""
import logging
import re

from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_SEGUNDOS = 60 * 10
LIMITE_COMPRAS = 40
TIMEOUT_SEGUNDOS = 12


def so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def formatar_cpf(digitos):
    """'00234483776' -> '002.344.837-76', que é como o MySQL guarda."""
    d = so_digitos(digitos)
    if len(d) == 11:
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    if len(d) == 14:                       # CNPJ
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    return d


def _conectar():
    import pymysql
    from simulator.sql_realizado import _mysql_config

    config = dict(_mysql_config())
    config['connect_timeout'] = TIMEOUT_SEGUNDOS
    config['read_timeout'] = TIMEOUT_SEGUNDOS
    config['cursorclass'] = pymysql.cursors.DictCursor
    return pymysql.connect(**config)


SQL_PRODUTOS = """
    SELECT data_da_venda AS data, pdv, nome_do_cliente AS cliente,
           nome_do_produto AS item, 'PRODUTO' AS tipo,
           qtde_vendida_do_produto AS qtde,
           `valor_líquido_de_venda_do_produto` AS valor,
           nome_do_vendedor AS vendedor
    FROM vendas_produto
    WHERE cpf_do_cliente = %s
    ORDER BY data_da_venda DESC
    LIMIT %s
"""

SQL_SERVICOS = """
    SELECT data_da_venda AS data, PDV AS pdv, Nome_do_cliente AS cliente,
           COALESCE(NULLIF(Serviço, ''), Plano_novo) AS item, 'SERVIÇO' AS tipo,
           1 AS qtde, Valor_do_plano_novo AS valor,
           Nome_do_vendedor AS vendedor
    FROM vendas_servicos
    WHERE CPF_do_cliente = %s
    ORDER BY data_da_venda DESC
    LIMIT %s
"""


def buscar_historico(cpf, usar_cache=True):
    """O que este CPF já comprou na rede.

    Devolve ``{'disponivel', 'encontrado', 'cpf', 'nome', 'total', 'primeira',
    'ultima', 'compras': [...]}``.
    """
    digitos = so_digitos(cpf)
    if len(digitos) not in (11, 14):
        return {'disponivel': True, 'encontrado': False, 'motivo': 'cpf_incompleto'}

    formatado = formatar_cpf(digitos)
    chave = f'vendas:cliente:{digitos}'
    if usar_cache:
        guardado = cache.get(chave)
        if guardado is not None:
            return guardado

    compras = []
    try:
        conexao = _conectar()
        try:
            with conexao.cursor() as cursor:
                for sql in (SQL_PRODUTOS, SQL_SERVICOS):
                    cursor.execute(sql, (formatado, LIMITE_COMPRAS))
                    compras.extend(cursor.fetchall())
        finally:
            conexao.close()
    except Exception as exc:               # base de outro sistema; nunca trava a tela
        logger.warning('Histórico do cliente indisponível: %s', exc)
        return {'disponivel': False, 'encontrado': False, 'cpf': formatado}

    compras = [c for c in compras if c.get('data')]
    compras.sort(key=lambda c: c['data'], reverse=True)

    nome = next((c['cliente'] for c in compras if c.get('cliente')), '')
    resultado = {
        'disponivel': True,
        'encontrado': bool(compras),
        'cpf': formatado,
        'nome': nome,
        'total': len(compras),
        'primeira': compras[-1]['data'] if compras else None,
        'ultima': compras[0]['data'] if compras else None,
        'compras': compras[:LIMITE_COMPRAS],
    }
    cache.set(chave, resultado, CACHE_SEGUNDOS)
    return resultado
