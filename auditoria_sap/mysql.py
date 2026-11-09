"""Leitura do banco MySQL do SAP.

Só SELECT: a visão `vw_auditoria_visao_geral` é fonte, e o portal nunca
escreve nela. O que o portal guarda (resolvida, quem marcou) fica no Postgres
do portal, no espelho local.

A credencial vem do ambiente, nunca do código. Por padrão é reaproveitada a
mesma do painel (`SISTEMA_PERFIL_MYSQL_URL`/`MYSQL_URI`, mesmo host e usuário),
trocando só o banco para ``SAP`` — que é exatamente como o pedido chegou. Para
apontar para outro servidor, basta definir ``SAP_MYSQL_URL``.
"""
import logging
from urllib.parse import unquote, urlparse, urlunparse

from decouple import config as _config_do_ambiente

logger = logging.getLogger(__name__)

BANCO = 'SAP'
VISAO = 'vw_auditoria_visao_geral'

# A visão faz várias junções e leva ~30 s para devolver as ~4 mil linhas: o
# tempo de leitura precisa ser folgado, senão a atualização morre no meio.
TEMPO_DE_CONEXAO = 10
TEMPO_DE_LEITURA = 300


class SapIndisponivel(RuntimeError):
    """Não deu para ler o SAP (sem credencial, fora do ar, view sumiu…)."""


def _do_ambiente(chave):
    """Valor do ambiente ou do .env — o mesmo caminho do resto do projeto."""
    try:
        return (_config_do_ambiente(chave, default='') or '').strip()
    except Exception:                                            # noqa: BLE001
        return ''


def endereco():
    """A URL do MySQL do SAP, ou '' se não houver credencial nenhuma."""
    direto = _do_ambiente('SAP_MYSQL_URL')
    if direto:
        return direto

    for chave in ('SISTEMA_PERFIL_MYSQL_URL', 'MYSQL_URI'):
        base = _do_ambiente(chave)
        if not base:
            continue
        partes = urlparse(base)
        if not partes.hostname:
            continue
        # mesma máquina, mesmo usuário, outro banco
        return urlunparse(partes._replace(path=f'/{BANCO}', query='', fragment=''))
    return ''


def _config():
    url = endereco()
    if not url:
        raise SapIndisponivel(
            'Sem credencial do MySQL: defina SAP_MYSQL_URL (ou SISTEMA_PERFIL_MYSQL_URL) no ambiente.')
    p = urlparse(url)
    if not p.hostname:
        raise SapIndisponivel('A URL do MySQL do SAP está sem host.')
    return {
        'host': p.hostname,
        'port': p.port or 3306,
        'user': unquote(p.username or ''),
        'password': unquote(p.password or ''),
        'database': (p.path or '').lstrip('/') or BANCO,
        'charset': 'utf8mb4',
        'connect_timeout': TEMPO_DE_CONEXAO,
        'read_timeout': TEMPO_DE_LEITURA,
    }


def conexao():
    """Conexão de leitura com o SAP. Levanta ``SapIndisponivel`` se não der."""
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:                                   # noqa: BLE001
        raise SapIndisponivel('PyMySQL não está instalado neste servidor.') from exc

    config = _config()
    try:
        return pymysql.connect(cursorclass=DictCursor, **config)
    except Exception as exc:                                     # noqa: BLE001
        logger.warning('SAP indisponível (%s@%s): %s', config['database'], config['host'], exc)
        raise SapIndisponivel(f'Não deu para conectar no MySQL do SAP: {exc}') from exc


def ler_formas_de_pagamento():
    """{nota fiscal: forma de pagamento} da SAP.zs103_final (a view da auditoria não traz)."""
    con = conexao()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT num_fat, forma_pagamento FROM zs103_final "
                        "WHERE num_fat <> '' AND forma_pagamento <> '' ORDER BY id")
            # A carga mais nova vem depois (id maior) e vale.
            return {str(r['num_fat']).strip(): str(r['forma_pagamento']).strip() for r in cur.fetchall()}
    except Exception as exc:                                     # noqa: BLE001
        raise SapIndisponivel(f'Falha lendo zs103_final: {exc}') from exc
    finally:
        try:
            con.close()
        except Exception:                                        # noqa: BLE001
            pass


def ler_visao_geral():
    """Todas as linhas da auditoria, como vieram da view (lista de dicts)."""
    con = conexao()
    try:
        with con.cursor() as cur:
            cur.execute(f'SELECT * FROM {VISAO}')
            return list(cur.fetchall())
    except Exception as exc:                                     # noqa: BLE001
        raise SapIndisponivel(f'Falha lendo {VISAO}: {exc}') from exc
    finally:
        try:
            con.close()
        except Exception:                                        # noqa: BLE001
            pass


def _consultar(sql, parametros=()):
    con = conexao()
    try:
        with con.cursor() as cur:
            cur.execute(sql, parametros)
            return list(cur.fetchall())
    except Exception as exc:                                     # noqa: BLE001
        raise SapIndisponivel(f'Falha lendo o SAP: {exc}') from exc
    finally:
        try:
            con.close()
        except Exception:                                        # noqa: BLE001
            pass


TIPO_RENOVA = 'ZVNB'


def ler_renovas(desde):
    """As ordens com Renova (tipo de faturamento ZVNB) da zs103_final desde a data.

    Uma linha por ordem: a carga mais nova (id maior) vale.
    """
    linhas = _consultar(
        "SELECT id, doc_vendas, num_fat, cnpj_cpf, dt_fatura, escr_vend, desc_material, hexadecimal, "
        "vl_faturado, desconto, status_nf FROM zs103_final "
        "WHERE tp_fat = %s AND dt_fatura >= %s ORDER BY id", (TIPO_RENOVA, desde))
    por_ordem = {}
    for linha in linhas:
        ordem = str(linha.get('doc_vendas') or '').strip()
        if ordem:
            por_ordem[ordem] = linha
    return list(por_ordem.values())


def ler_vendas_vivogo(desde):
    """Vendas do Vivo Go (com CPF do cliente e a nota do SAP) desde a data.

    Vem da vw_conferencia_vendas_produtos — a ponte CPF ↔ venda do Vivo Go ↔ nota.
    ~10 s para três meses.
    """
    return _consultar(
        "SELECT ID_VENDA, CPF_VIVOGO_NORMALIZADO, CPF_SAP_NORMALIZADO, NUM_FAT_SAP, PDV, NOME_CLIENTE "
        "FROM vw_conferencia_vendas_produtos WHERE DATA_VENDA >= %s", (desde,))
