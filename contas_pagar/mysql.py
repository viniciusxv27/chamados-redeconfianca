"""O histórico oficial no MySQL ``contas_pagar`` (o Power BI lê de lá).

Tabelas (já existem; o portal nunca roda DDL nelas):
- ``cp_titulos_programados``: um registro por título programado.
- ``cp_saldos_bancos``: saldos por banco e por dia (chave: data_pagamento + banco).

Publicar a mesma data de pagamento substitui os registros daquele dia, como no
sistema antigo. A credencial é a do Vivo GO (``VIVOGO_MYSQL_URL``: mesmo host,
usuário com permissão em ``contas_pagar``) trocando só o banco; para apontar para
outro servidor, defina ``CONTAS_PAGAR_MYSQL_URL``.
"""
import logging
from urllib.parse import unquote, urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

BANCO = 'contas_pagar'
TEMPO_DE_CONEXAO = 10
TEMPO_DE_LEITURA = 60


class ContasPagarIndisponivel(RuntimeError):
    """Não deu para falar com o MySQL do contas_pagar (sem credencial, fora do ar…)."""


def _do_ambiente(chave):
    valor = getattr(settings, chave, '') or ''
    if not valor:
        try:
            from decouple import config
            valor = config(chave, default='') or ''
        except Exception:  # noqa: BLE001
            valor = ''
    return str(valor).strip()


def _config():
    url = _do_ambiente('CONTAS_PAGAR_MYSQL_URL') or _do_ambiente('VIVOGO_MYSQL_URL')
    p = urlparse(url) if url else None
    if not p or not p.hostname:
        raise ContasPagarIndisponivel('Sem credencial do MySQL: VIVOGO_MYSQL_URL não está configurada.')
    banco = BANCO
    if _do_ambiente('CONTAS_PAGAR_MYSQL_URL'):
        banco = (p.path or '').lstrip('/') or BANCO
    return {'host': p.hostname, 'port': p.port or 3306, 'user': unquote(p.username or ''),
            'password': unquote(p.password or ''), 'database': banco, 'charset': 'utf8mb4',
            'connect_timeout': TEMPO_DE_CONEXAO, 'read_timeout': TEMPO_DE_LEITURA,
            'write_timeout': TEMPO_DE_LEITURA, 'autocommit': False}


def conexao():
    """Conexão com o MySQL. Levanta ``ContasPagarIndisponivel`` se não der."""
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:  # noqa: BLE001
        raise ContasPagarIndisponivel('PyMySQL não está instalado neste servidor.') from exc
    cfg = _config()
    try:
        return pymysql.connect(cursorclass=DictCursor, **cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning('MySQL do contas_pagar indisponível (%s): %s', cfg['host'], exc)
        raise ContasPagarIndisponivel(f'Não deu para conectar no MySQL do contas a pagar: {exc}') from exc


def _fechar(con):
    try:
        con.close()
    except Exception:  # noqa: BLE001
        pass


def _ler(sql, parametros=()):
    con = conexao()
    try:
        with con.cursor() as cur:
            cur.execute(sql, parametros)
            return list(cur.fetchall())
    except ContasPagarIndisponivel:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ContasPagarIndisponivel(f'Falha lendo o MySQL do contas a pagar: {exc}') from exc
    finally:
        _fechar(con)


# ── Leitura ────────────────────────────────────────────────────────────────
def ler_periodo(ini, fim):
    """(títulos, saldos) com data de pagamento entre ini e fim."""
    con = conexao()
    try:
        with con.cursor() as c:
            c.execute("""SELECT data_pagamento, vencimento, fornecedor, filial, observacoes, tipo_macro,
                                banco_pagador AS banco, valor_liquido AS valor, despesa
                         FROM cp_titulos_programados WHERE data_pagamento BETWEEN %s AND %s
                         ORDER BY data_pagamento, valor_liquido""", (ini, fim))
            titulos = list(c.fetchall())
            c.execute("""SELECT * FROM cp_saldos_bancos WHERE data_pagamento BETWEEN %s AND %s
                         ORDER BY data_pagamento, ordem""", (ini, fim))
            saldos = list(c.fetchall())
        return titulos, saldos
    except Exception as exc:  # noqa: BLE001
        raise ContasPagarIndisponivel(f'Falha lendo o MySQL do contas a pagar: {exc}') from exc
    finally:
        _fechar(con)


def historico():
    """Um resumo por dia publicado, do mais novo para o mais antigo."""
    linhas = _ler("""SELECT data_pagamento, COUNT(*) AS qtd, SUM(valor_liquido) AS valor,
                            MIN(vencimento) AS venc_ini, MAX(vencimento) AS venc_fim, MAX(publicado_em) AS publicado_em
                     FROM cp_titulos_programados GROUP BY data_pagamento ORDER BY data_pagamento DESC""")
    return linhas


def ultimos_saldos():
    """Saldos do último dia publicado, para pré-preencher uma nova importação."""
    return _ler("""SELECT * FROM cp_saldos_bancos WHERE data_pagamento =
                   (SELECT MAX(data_pagamento) FROM cp_saldos_bancos)""")


def resumo_do_dia(dia):
    linhas = _ler("SELECT COUNT(*) AS qtd, SUM(valor_liquido) AS valor FROM cp_titulos_programados "
                  "WHERE data_pagamento = %s", (dia,))
    return linhas[0] if linhas else {'qtd': 0, 'valor': 0}


# ── Escrita ────────────────────────────────────────────────────────────────
SQL_TITULO = """INSERT INTO cp_titulos_programados
    (data_pagamento, vencimento, fornecedor, cnpj_cpf_fornecedor, filial, observacoes, plano_contas,
     despesa, agging, tipo_macro, forma_pagamento, num_movimento, num_documento, nota_fiscal, debito,
     banco_pagador, valor_liquido, publicado_em)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
SQL_SALDO = """INSERT INTO cp_saldos_bancos
    (data_pagamento, banco, ordem, aplicacao, conta_garantida, saldo, total_disponivel, pagamentos,
     transferencia, saldo_final, publicado_em) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""


def gravar_dia(dia, linhas_titulos, linhas_saldo):
    """Substitui o dia inteiro numa transação só. Devolve o que havia antes ({qtd, valor})."""
    con = conexao()
    try:
        with con.cursor() as c:
            c.execute("SELECT COUNT(*) AS qtd, SUM(valor_liquido) AS valor FROM cp_titulos_programados "
                      "WHERE data_pagamento = %s", (dia,))
            antes = c.fetchone() or {}
            c.execute('DELETE FROM cp_titulos_programados WHERE data_pagamento=%s', (dia,))
            c.execute('DELETE FROM cp_saldos_bancos WHERE data_pagamento=%s', (dia,))
            if linhas_titulos:
                c.executemany(SQL_TITULO, linhas_titulos)
            if linhas_saldo:
                c.executemany(SQL_SALDO, linhas_saldo)
        con.commit()
        return antes
    except Exception as exc:  # noqa: BLE001
        try:
            con.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise ContasPagarIndisponivel(f'Falha gravando no MySQL do contas a pagar (nada foi alterado): {exc}') from exc
    finally:
        _fechar(con)


def excluir_dia(dia):
    """Apaga títulos e saldos do dia. Devolve {qtd, valor, saldos} do que foi apagado."""
    con = conexao()
    try:
        with con.cursor() as c:
            c.execute("SELECT COUNT(*) AS qtd, SUM(valor_liquido) AS valor FROM cp_titulos_programados "
                      "WHERE data_pagamento = %s", (dia,))
            antes = dict(c.fetchone() or {})
            c.execute('SELECT COUNT(*) AS n FROM cp_saldos_bancos WHERE data_pagamento = %s', (dia,))
            antes['saldos'] = (c.fetchone() or {}).get('n') or 0
            c.execute('DELETE FROM cp_titulos_programados WHERE data_pagamento=%s', (dia,))
            c.execute('DELETE FROM cp_saldos_bancos WHERE data_pagamento=%s', (dia,))
        con.commit()
        return antes
    except Exception as exc:  # noqa: BLE001
        try:
            con.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise ContasPagarIndisponivel(f'Falha excluindo o dia no MySQL (nada foi alterado): {exc}') from exc
    finally:
        _fechar(con)
