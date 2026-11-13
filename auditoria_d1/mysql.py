# -*- coding: utf-8 -*-
"""Acesso ao MySQL (banco vivogo) — porte do db.py do sistema "Auditoria D-1".

A tabela de origem vivogo.vendas_servicos é só lida (é recarregada pela integração do VivoGo e não tem
colunas de auditoria). O resultado da verificação fica em vivogo.verificacao_vendas, uma linha por serviço
(ID_do_serviço é único na origem), e cada gravação vai para o histórico verificacao_vendas_log.

As tabelas (verificacao_vendas, verificacao_vendas_log, cruzamento_importacoes, cruzamento_sugestoes) e a visão
vw_vendas_auditoria JÁ existem no servidor e alimentam o Power BI; o sistema antigo continua gravando nelas em
paralelo. Por isso o portal NUNCA roda DDL aqui (o antigo criava tudo "na primeira execução"): só lê e grava
com as mesmas colunas, os mesmos valores e os mesmos eventos no log.

Conexão: VIVOGO_MYSQL_URL (settings ou .env), a mesma de vendas/vivogo.py.
"""
import datetime
import json
import logging
import re
import time
from contextlib import contextmanager
from urllib.parse import unquote, urlparse

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

BANCO = "vivogo"

# Escopo da esteira: serviços técnicos que entram na verificação (mesmo recorte da planilha base).
# Fica de fora: pré-pago/trocas sem serviço técnico, Troca Fidelizada, Downgrades e Alta Pré com D0.
SERVICOS_FORA = ("Troca Fidelizada", "Alta Pré com D0")
PREFIXOS_FORA = ("Downgrade",)


class D1Indisponivel(RuntimeError):
    """Não deu para falar com o MySQL do VivoGo (sem credencial, fora do ar, conexão caiu)."""


def _url():
    from django.conf import settings
    url = getattr(settings, "VIVOGO_MYSQL_URL", "") or ""
    if not url:
        try:
            from decouple import config
            url = config("VIVOGO_MYSQL_URL", default="") or ""
        except Exception:  # noqa: BLE001
            url = ""
    return url.strip()


def configurado():
    return bool(urlparse(_url()).hostname)


def conectar():
    try:
        import pymysql
        import pymysql.cursors
    except ImportError as exc:  # noqa: BLE001
        raise D1Indisponivel("PyMySQL não está instalado neste servidor.") from exc
    url = urlparse(_url())
    if not url.hostname:
        raise D1Indisponivel("VIVOGO_MYSQL_URL não configurada.")
    try:
        return pymysql.connect(host=url.hostname, port=url.port or 3306, user=unquote(url.username or ""),
                               password=unquote(url.password or ""), database=url.path.lstrip("/") or BANCO,
                               charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=False,
                               connect_timeout=15, read_timeout=120, write_timeout=120)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auditoria D-1: MySQL do VivoGo indisponível: %s", exc)
        raise D1Indisponivel(f"Sem conexão com o banco do VivoGo: {exc}") from exc


@contextmanager
def cursor():
    con = conectar()
    try:
        with con.cursor() as cur:
            yield cur
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:  # noqa: BLE001 — conexão já caiu; o erro original é o que interessa
            pass
        raise
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


# Erros de rede/conexão (o servidor às vezes derruba a conexão): leituras tentam de novo
ERROS_CONEXAO = {2003, 2006, 2013, 0}


def _erro_de_conexao(e):
    try:
        import pymysql
    except ImportError:  # noqa: BLE001
        return False
    return isinstance(e, (pymysql.OperationalError, pymysql.InterfaceError))


def consultar(sql, params=(), tentativas=3):
    for t in range(tentativas):
        try:
            with cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        except D1Indisponivel:
            if t == tentativas - 1:
                raise
            time.sleep(0.5 * (t + 1))
        except Exception as e:  # noqa: BLE001
            if not _erro_de_conexao(e) or t == tentativas - 1 or (e.args and e.args[0] not in ERROS_CONEXAO):
                raise
            time.sleep(0.5 * (t + 1))


CHAVE_PDVS = "auditoria_d1:pdvs"


def pdvs():
    """Filiais existentes (cache de 10 min). A tabela só tem índice em (PDV, data_da_venda): filtrar por
    PDV IN (todas) + data vira um range scan no índice (~0,3 s) em vez de varrer a tabela inteira (~10 s)."""
    lista = cache.get(CHAVE_PDVS)
    if lista is None:
        linhas = consultar("SELECT DISTINCT `PDV` AS pdv FROM vendas_servicos WHERE `PDV` IS NOT NULL")
        lista = [r["pdv"] for r in linhas]
        cache.set(CHAVE_PDVS, lista, 600)
    return lista


# Filtro do escopo da esteira (mesmo recorte da planilha base)
def filtro_escopo(alias="s"):
    lista = pdvs() or [""]
    partes = [f"{alias}.`PDV` IN ({', '.join(['%s'] * len(lista))})", f"COALESCE({alias}.`Serviço_Técnico`, '') <> ''"]
    if SERVICOS_FORA:
        partes.append(f"{alias}.`Serviço_Técnico` NOT IN ({', '.join(['%s'] * len(SERVICOS_FORA))})")
    for _ in PREFIXOS_FORA:
        partes.append(f"{alias}.`Serviço_Técnico` NOT LIKE %s")
    params = list(lista) + list(SERVICOS_FORA) + [p + "%" for p in PREFIXOS_FORA]
    return " AND ".join(partes), params


COLUNAS_VENDA = """
    s.`ID_do_serviço`           AS id_servico,
    s.`ID_da_venda`             AS id_venda,
    s.`data_da_venda`           AS data_venda,
    s.`PDV`                     AS pdv,
    s.`Serviço_Técnico`         AS servico,
    s.`Serviço`                 AS servico_grupo,
    s.`pilar`                   AS pilar,
    s.`Nome_do_vendedor`        AS vendedor,
    s.`Nome_do_cliente`         AS cliente,
    s.`CPF_do_cliente`          AS cpf,
    s.`Documento_do_cliente`    AS documento,
    s.`Plano_novo`              AS plano,
    s.`Plano_antigo`            AS plano_antigo,
    s.`Nº_acesso`               AS acesso,
    s.`Nº_portabilidade`        AS portabilidade,
    s.`Valor_do_plano_novo`     AS valor_plano,
    s.`Valor_do_plano_antigo`   AS valor_plano_antigo,
    s.`Receita`                 AS receita,
    s.`Status_do_Serviço`       AS status_servico,
    s.`Status_BKO`              AS status_bko,
    s.`Data_da_última_ativação` AS ultima_ativacao,
    s.`Data_de_ativação`        AS data_ativacao,
    s.`Cidade_do_cliente`       AS cidade_cliente,
    s.`UF_do_cliente`           AS uf_cliente,
    s.`endereço_do_cliente`     AS endereco,
    s.`Observação`              AS obs_venda,
    s.`Grupo_do_tipo_de_plano`  AS grupo_plano,
    s.`Dia_de_vencimento`       AS vencimento,
    s.`Data_da_instalação`      AS data_instalacao,
    s.`iccid_3_g`               AS iccid,
    s.`Conta_Digital`           AS conta_digital,
    s.`Fidelizado`              AS fidelizado,
    s.`Zerar_remuneração`       AS zerar_remuneracao,
    s.`Nº_protocolo`            AS protocolo,
    s.`Nº_Protocolo_GED`        AS protocolo_ged,
    s.`Motivo_de_cancelamento`  AS motivo_cancelamento,
    s.`Status_da_Instalação`    AS status_instalacao,
    s.`Data_de_cancelamento_do_protocolo` AS data_cancelamento,
    s.`usuário_cancelamento_do_protocolo` AS usuario_cancelamento,
    v.status            AS v_status,
    v.correcao_vivogo   AS v_correcao,
    v.tipo_penalidade   AS v_penalidade,
    v.acao_necessaria   AS v_acao,
    v.receita_correta   AS v_receita_correta,
    v.plano_correto     AS v_plano_correto,
    v.linha_correta     AS v_linha_correta,
    v.documento_correto AS v_documento_correto,
    v.receita_validada  AS v_receita_validada,
    v.alterar_vivogo    AS v_alterar_vivogo,
    v.observacao        AS v_observacao,
    v.verificado_por    AS v_por,
    v.origem            AS v_origem,
    v.confianca         AS v_confianca,
    v.cruzamento_anomes AS v_anomes,
    v.receita_original  AS v_receita_original,
    v.contestacao_decisao     AS v_contestacao_decisao,
    v.contestacao_solicitante AS v_contestacao_solicitante,
    v.contestacao_motivo      AS v_contestacao_motivo,
    v.contestacao_por         AS v_contestacao_por,
    v.contestacao_em          AS v_contestacao_em,
    v.divergencia_contestada  AS v_divergencia_contestada,
    v.atualizado_em     AS v_em
"""


def vendas_periodo(inicio, fim):
    esc, params = filtro_escopo()
    sql = f"""SELECT {COLUNAS_VENDA}
              FROM vendas_servicos s
              LEFT JOIN verificacao_vendas v ON v.id_servico = s.`ID_do_serviço`
              WHERE s.`data_da_venda` BETWEEN %s AND %s AND {esc}
              ORDER BY s.`PDV`, s.`ID_da_venda`, s.`ID_do_serviço`"""
    return consultar(sql, [inicio, fim] + params)


def vendas_divergentes(inicio, fim):
    """Serviços DIVERGENTES (e CONTESTADOS, que voltam a venda) do período com os dados de origem. Parte das
    verificações (tabela pequena) e busca a origem pelos pares (PDV, data) — que usam o índice — em vez de varrer
    o mês inteiro."""
    alvos = consultar("""SELECT id_servico, pdv, data_venda FROM verificacao_vendas
                         WHERE status IN ('DIVERGENTE', 'CONTESTADA') AND data_venda BETWEEN %s AND %s""", [inicio, fim])
    if not alvos:
        return []
    pares = sorted({(a["pdv"], a["data_venda"]) for a in alvos})
    ids = [a["id_servico"] for a in alvos]
    sql = f"""SELECT {COLUNAS_VENDA}
              FROM vendas_servicos s
              JOIN verificacao_vendas v ON v.id_servico = s.`ID_do_serviço`
              WHERE (s.`PDV`, s.`data_da_venda`) IN ({', '.join(['(%s, %s)'] * len(pares))})
                AND s.`ID_do_serviço` IN ({', '.join(['%s'] * len(ids))})
              ORDER BY s.`data_da_venda`, s.`PDV`, s.`ID_da_venda`"""
    return consultar(sql, [x for p in pares for x in p] + ids)


def indice_mes(inicio, fim):
    """Uma linha enxuta por serviço do mês: base dos totais por dia e dos alertas que olham o mês inteiro
    (acesso de outro cliente, documento com outro nome, venda em duplicidade).
    É a consulta mais pesada (milhares de linhas lidas na tabela de origem), por isso fica em cache."""
    esc, params = filtro_escopo()
    sql = f"""SELECT s.`ID_do_serviço` AS id_servico, s.`ID_da_venda` AS id_venda, s.`data_da_venda` AS data_venda,
                     s.`Nº_acesso` AS acesso, s.`Plano_novo` AS plano, s.`Serviço_Técnico` AS servico,
                     s.`Receita` AS receita, s.`CPF_do_cliente` AS cpf, s.`Documento_do_cliente` AS documento,
                     s.`Nome_do_cliente` AS cliente
              FROM vendas_servicos s
              WHERE s.`data_da_venda` BETWEEN %s AND %s AND {esc}"""
    return consultar(sql, [inicio, fim] + params)


def verificacoes_mes(inicio, fim):
    return consultar("SELECT id_servico, status FROM verificacao_vendas WHERE data_venda BETWEEN %s AND %s", [inicio, fim])


def servicos_por_id(ids, inicio, fim):
    """Dados de origem dos serviços a gravar. O intervalo de datas é obrigatório: ID_do_serviço não tem índice,
    então a busca vai pelo índice (PDV, data_da_venda) e só depois filtra os IDs."""
    if not ids:
        return []
    esc, params = filtro_escopo()
    sql = f"""SELECT s.`ID_do_serviço` AS id_servico, s.`ID_da_venda` AS id_venda, s.`data_da_venda` AS data_venda,
                     s.`PDV` AS pdv, s.`Receita` AS receita
              FROM vendas_servicos s
              WHERE s.`data_da_venda` BETWEEN %s AND %s AND {esc}
                AND s.`ID_do_serviço` IN ({', '.join(['%s'] * len(ids))})"""
    return consultar(sql, [inicio, fim] + params + list(ids))


# ---------------------------------------------------------------- busca geral (nº da venda / CPF em qualquer dia)
COLUNAS_BUSCA = """
    s.`ID_do_serviço`   AS id_servico,
    s.`ID_da_venda`     AS id_venda,
    s.`data_da_venda`   AS data_venda,
    s.`PDV`             AS pdv,
    s.`Serviço_Técnico` AS servico,
    s.`pilar`           AS pilar,
    s.`Nome_do_cliente` AS cliente,
    s.`CPF_do_cliente`  AS cpf,
    s.`Documento_do_cliente` AS documento,
    s.`Plano_novo`      AS plano,
    s.`Receita`         AS receita,
    v.status            AS v_status,
    v.correcao_vivogo   AS v_correcao,
    v.origem            AS v_origem,
    v.contestacao_decisao AS v_contestacao_decisao
"""


def _condicao_busca(tipo, valor):
    """tipo 'id': nº da venda ou do serviço; 'doc': CPF/CNPJ só com dígitos (a origem guarda com máscara, mas
    aceita os dois jeitos)."""
    if tipo == "id":
        return "(s.`ID_da_venda` = %s OR s.`ID_do_serviço` = %s)", [valor, valor]
    from .formato import doc_vivogo
    formas = [doc_vivogo(valor), valor]
    return "(s.`CPF_do_cliente` IN (%s, %s) OR s.`Documento_do_cliente` IN (%s, %s))", formas + formas


def datas_verificadas(id_venda_ou_servico):
    """Datas das vendas já verificadas com esse nº (verificacao_vendas tem índice em id_venda e chave em id_servico):
    acha a venda de qualquer época em ~0,1 s, sem varrer a origem."""
    return sorted({r["data_venda"] for r in consultar(
        "SELECT DISTINCT data_venda FROM verificacao_vendas WHERE id_venda = %s OR id_servico = %s",
        [id_venda_ou_servico, id_venda_ou_servico]) if r["data_venda"]})


def buscar_vendas(tipo, valor, inicio=None, fim=None, datas=None, limite=200):
    """Serviços do escopo da esteira com esse nº de venda/serviço ou CPF/CNPJ, do mais recente para o mais antigo
    (até limite + 1, para a tela saber que cortou).

    Desempenho: vendas_servicos só tem índice em (PDV, data_da_venda) e (data_da_venda, Nome_do_vendedor); ID e CPF não
    têm. Por isso a busca é sempre num período (ou em datas exatas) e ordena SÓ pela data: assim o MySQL percorre o
    índice de data de trás para frente e para no LIMIT. Ordenar também pelo ID faz o otimizador trocar o índice pela
    varredura da tabela inteira a partir de ~6 meses (medido em 13/11/2026, só leitura: 3 meses 0,1–0,4 s, 9 meses
    0,3–0,5 s, 12 meses 0,4–0,6 s; com data + ID, 9–12 meses viram 1,2–3,4 s). O desempate por venda/serviço é aqui."""
    esc, params = filtro_escopo()
    cond, pcond = _condicao_busca(tipo, valor)
    if datas:
        periodo, pper = f"s.`data_da_venda` IN ({', '.join(['%s'] * len(datas))})", list(datas)
    else:
        periodo, pper = "s.`data_da_venda` BETWEEN %s AND %s", [inicio, fim]
    sql = f"""SELECT {COLUNAS_BUSCA}
              FROM vendas_servicos s
              LEFT JOIN verificacao_vendas v ON v.id_servico = s.`ID_do_serviço`
              WHERE {periodo} AND {esc} AND {cond}
              ORDER BY s.`data_da_venda` DESC
              LIMIT %s"""
    linhas = consultar(sql, pper + params + pcond + [limite + 1])
    return sorted(linhas, key=ordem_busca, reverse=True)


def ordem_busca(r):
    """Chave da busca geral (usar com reverse=True): data mais recente, venda maior, serviço menor."""
    def num(x):
        return int(re.sub(r"\D", "", str(x or "")) or 0)
    return (r["data_venda"], num(r["id_venda"]), -num(r["id_servico"]))


CAMPOS_VERIF = ["id_servico", "id_venda", "data_venda", "pdv", "status", "correcao_vivogo", "tipo_penalidade",
                "acao_necessaria", "receita_original", "receita_correta", "plano_correto", "linha_correta",
                "documento_correto", "receita_validada", "alterar_vivogo", "observacao", "verificado_por"]

# Colunas acrescentadas depois da primeira versão do sistema antigo (já existem no servidor)
COLUNAS_NOVAS = ("origem", "confianca", "cruzamento_anomes", "importacao_id", "verificacao_anterior",
                 "contestacao_decisao", "contestacao_solicitante", "contestacao_motivo", "contestacao_por",
                 "contestacao_em", "divergencia_contestada")
CONTESTACAO = [c for c in COLUNAS_NOVAS if c.startswith("contestacao_")] + ["divergencia_contestada"]


def agora_local():
    """Horário de Brasília sem fuso (o servidor MySQL está em UTC; NOW() lá gravaria 3 h adiantado).
    O sistema antigo grava a hora da máquina (Brasília): o portal grava igual."""
    return timezone.localtime().replace(tzinfo=None, microsecond=0)


def gravar_verificacoes(registros, usuario):
    """Upsert das verificações + histórico. Mantém verificado_em da primeira verificação."""
    if not registros:
        return 0
    cols = ", ".join(CAMPOS_VERIF)
    ph = ", ".join(["%s"] * len(CAMPOS_VERIF))
    upd = ", ".join(f"{c} = VALUES({c})" for c in CAMPOS_VERIF if c != "id_servico")
    sql = f"""INSERT INTO verificacao_vendas ({cols}, verificado_em, atualizado_em)
              VALUES ({ph}, %s, %s)
              ON DUPLICATE KEY UPDATE {upd}, atualizado_em = VALUES(atualizado_em), origem = 'MANUAL',
                 confianca = NULL, cruzamento_anomes = NULL, importacao_id = NULL, verificacao_anterior = NULL,
                 contestacao_decisao = NULL, contestacao_solicitante = NULL, contestacao_motivo = NULL,
                 contestacao_por = NULL, contestacao_em = NULL, divergencia_contestada = NULL"""
    agora = agora_local()
    with cursor() as cur:
        cur.executemany(sql, [[r.get(c) for c in CAMPOS_VERIF] + [agora, agora] for r in registros])
        cur.executemany(
            "INSERT INTO verificacao_vendas_log (id_servico, evento, dados, usuario, em) VALUES (%s, 'SALVAR', %s, %s, %s)",
            [[r["id_servico"], json.dumps(r, default=str, ensure_ascii=False), usuario, agora] for r in registros])
    return len(registros)


def reabrir(id_servico, usuario):
    with cursor() as cur:
        cur.execute("DELETE FROM verificacao_vendas WHERE id_servico = %s", [id_servico])
        cur.execute("INSERT INTO verificacao_vendas_log (id_servico, evento, dados, usuario, em) VALUES (%s, 'REABRIR', NULL, %s, %s)",
                    [id_servico, usuario, agora_local()])
        return cur.rowcount


def contestar(id_servico, decisao, solicitante, motivo, usuario):
    """Registra a contestação de uma divergência.

    PROCEDENTE: a venda volta — status CONTESTADA, receita validada = receita original e alterar_vivogo = 1 (o
    Exportar gera a linha que desfaz a alteração no VivoGo). A divergência como estava fica em
    divergencia_contestada e no histórico; as automações não remarcam a venda.
    IMPROCEDENTE: a divergência continua; só registra quem contestou, o motivo e a decisão.
    """
    agora = agora_local()
    with cursor() as cur:
        cur.execute("SELECT * FROM verificacao_vendas WHERE id_servico = %s FOR UPDATE", [id_servico])
        atual = cur.fetchone()
        if not atual or atual["status"] != "DIVERGENTE":
            raise ValueError("Só uma venda divergente pode ser contestada.")
        dados = {"contestacao_decisao": decisao, "contestacao_solicitante": solicitante,
                 "contestacao_motivo": motivo, "contestacao_por": usuario, "contestacao_em": agora}
        if decisao == "PROCEDENTE":
            dados.update(status="CONTESTADA", acao_necessaria="VOLTAR VENDA", receita_validada=atual["receita_original"],
                         alterar_vivogo=1, origem="CONTESTACAO", divergencia_contestada=_instantaneo(atual),
                         atualizado_em=agora)
        cur.execute(f"UPDATE verificacao_vendas SET {', '.join(f'{c} = %s' for c in dados)} WHERE id_servico = %s",
                    list(dados.values()) + [id_servico])
        cur.execute("INSERT INTO verificacao_vendas_log (id_servico, evento, dados, usuario, em) VALUES (%s, 'CONTESTACAO', %s, %s, %s)",
                    [id_servico, json.dumps({**dados, "divergencia": {k: atual[k] for k in (
                        "correcao_vivogo", "tipo_penalidade", "receita_correta", "receita_validada", "origem", "confianca",
                        "observacao", "verificado_por")}}, default=str, ensure_ascii=False), usuario, agora])
    return dados


def desfazer_contestacao(id_servico, usuario):
    """Volta a divergência que existia antes da contestação procedente."""
    agora = agora_local()
    with cursor() as cur:
        cur.execute("SELECT * FROM verificacao_vendas WHERE id_servico = %s FOR UPDATE", [id_servico])
        atual = cur.fetchone()
        if not atual or not (atual["contestacao_decisao"] or atual["status"] == "CONTESTADA"):
            raise ValueError("Esta venda não tem contestação registrada.")
        if atual["status"] == "CONTESTADA" and atual["divergencia_contestada"]:
            dados = json.loads(atual["divergencia_contestada"])
            dados = {k: v for k, v in dados.items() if k not in COLUNAS_NOVAS or k in ("origem", "confianca",
                     "cruzamento_anomes", "importacao_id", "verificacao_anterior")}
            dados.update({c: None for c in CONTESTACAO}, atualizado_em=agora)
            cols = list(dados)
            cur.execute(f"REPLACE INTO verificacao_vendas ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                        [dados[c] for c in cols])
        else:
            cur.execute(f"UPDATE verificacao_vendas SET {', '.join(f'{c} = NULL' for c in CONTESTACAO)} WHERE id_servico = %s",
                        [id_servico])
        cur.execute("INSERT INTO verificacao_vendas_log (id_servico, evento, dados, usuario, em) VALUES (%s, 'DESF_CONTEST', NULL, %s, %s)",
                    [id_servico, usuario, agora])


def historico(id_servico):
    with cursor() as cur:
        cur.execute("SELECT evento, dados, usuario, em FROM verificacao_vendas_log WHERE id_servico = %s ORDER BY id DESC LIMIT 20",
                    [id_servico])
        return list(cur.fetchall())


# ---------------------------------------------------------------- cruzamentos automáticos (SVA e Fixa)
def _instantaneo(linha):
    """Verificação como estava antes da automação/contestação (para restaurar depois). Guarda origem, confiança
    e o vínculo com a importação; não guarda os campos de contestação."""
    fica = ("origem", "confianca", "cruzamento_anomes", "importacao_id", "verificacao_anterior")
    return json.dumps({k: v for k, v in linha.items() if k not in COLUNAS_NOVAS or k in fica}, default=str, ensure_ascii=False)


def _restaurar(cur, id_servico, anterior):
    if anterior:
        dados = {k: v for k, v in json.loads(anterior).items() if k not in COLUNAS_NOVAS}
        cols = list(dados)
        cur.execute(f"REPLACE INTO verificacao_vendas ({', '.join(cols)}, origem) VALUES ({', '.join(['%s'] * len(cols))}, 'MANUAL')",
                    [dados[c] for c in cols])
    else:
        cur.execute("DELETE FROM verificacao_vendas WHERE id_servico = %s", [id_servico])


def _meses(anomes):
    return [anomes] if isinstance(anomes, str) else sorted(set(anomes))


def _contestadas(cur, ids):
    if not ids:
        return set()
    cur.execute(f"SELECT id_servico FROM verificacao_vendas WHERE status = 'CONTESTADA' AND id_servico IN ({', '.join(['%s'] * len(ids))})",
                list(ids))
    return {r["id_servico"] for r in cur.fetchall()}


def aplicar_cruzamento(origem, anomes, meta, marcacoes, sugestoes, resultado, usuario):
    """Grava o resultado de um cruzamento numa transação.

    A automação prevalece sobre a verificação manual (a manual fica guardada em verificacao_anterior), menos sobre
    uma contestação procedente (a venda que voltou não é remarcada).
    Uma base nova do mesmo AnoMes substitui a anterior: marcações automáticas desse AnoMes que não vieram na base
    nova são desfeitas (volta a verificação manual que existia, ou o serviço volta a pendente).
    anomes: o AnoMes da base, ou a lista de AnoMes que ela cobre (base de fixa traz mais de um mês); cada marcação
            e sugestão pode trazer o próprio "anomes" (o mês da venda).
    marcacoes: [{id_servico, id_venda, data_venda, pdv, receita, correcao, penalidade, acao, observacao, confianca,
                 verificado_por, anomes?}]
    sugestoes: [{id_venda, id_servico, data_venda, servico_base, confianca, motivo, correcao?, penalidade?, anomes?}]
    """
    agora = agora_local()
    meses = _meses(anomes)
    cont = {"revertidas": 0, "manuais_sobrescritas": 0, "contestadas_mantidas": 0}
    with cursor() as cur:
        preservar = _contestadas(cur, {m["id_servico"] for m in marcacoes})
        cont["contestadas_mantidas"] = len(preservar)
        marcacoes = [m for m in marcacoes if m["id_servico"] not in preservar]
        novos = {m["id_servico"] for m in marcacoes}
        cur.execute("""INSERT INTO cruzamento_importacoes (origem, anomes, meses, arquivo, base_de, base_ate, linhas, aplicadas,
                       sugestoes, nao_existe, resultado, usuario, em) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [origem, meses[-1], ",".join(meses), meta.get("arquivo"), meta.get("base_de"), meta.get("base_ate"),
                     meta.get("linhas", 0), len(marcacoes), len(sugestoes), meta.get("nao_existe", 0),
                     json.dumps(resultado, default=str, ensure_ascii=False), usuario, agora])
        imp = cur.lastrowid

        # 1. marcações automáticas dos mesmos AnoMes que saíram na base nova
        cur.execute(f"""SELECT id_servico, verificacao_anterior FROM verificacao_vendas
                        WHERE origem = %s AND cruzamento_anomes IN ({', '.join(['%s'] * len(meses))})""", [origem] + meses)
        for v in list(cur.fetchall()):
            if v["id_servico"] not in novos:
                _restaurar(cur, v["id_servico"], v["verificacao_anterior"])
                cur.execute("INSERT INTO verificacao_vendas_log (id_servico, evento, dados, usuario, em) VALUES (%s, 'REVERTER', %s, %s, %s)",
                            [v["id_servico"], json.dumps({"origem": origem, "anomes": meses, "importacao": imp}), usuario, agora])
                cont["revertidas"] += 1

        # 2. novas marcações (prevalecem sobre a verificação manual)
        existentes = {}
        if novos:
            cur.execute(f"SELECT * FROM verificacao_vendas WHERE id_servico IN ({', '.join(['%s'] * len(novos))})", list(novos))
            existentes = {r["id_servico"]: r for r in cur.fetchall()}
        for m in marcacoes:
            atual = existentes.get(m["id_servico"])
            mesma_origem = bool(atual) and atual.get("origem") == origem
            if mesma_origem:
                anterior = atual.get("verificacao_anterior")
            elif atual:
                anterior = _instantaneo(atual)
                cont["manuais_sobrescritas"] += 1
            else:
                anterior = None
            cur.execute("""REPLACE INTO verificacao_vendas (id_servico, id_venda, data_venda, pdv, status, correcao_vivogo,
                           tipo_penalidade, acao_necessaria, receita_original, receita_correta, plano_correto, linha_correta,
                           documento_correto, receita_validada, alterar_vivogo, observacao, verificado_por, verificado_em,
                           atualizado_em, origem, confianca, cruzamento_anomes, importacao_id, verificacao_anterior)
                           VALUES (%s,%s,%s,%s,'DIVERGENTE',%s,%s,%s,%s,NULL,NULL,NULL,NULL,0,1,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        [m["id_servico"], m["id_venda"], m["data_venda"], m["pdv"], m["correcao"], m["penalidade"], m["acao"],
                         m["receita"], m["observacao"][:500], m["verificado_por"],
                         atual["verificado_em"] if mesma_origem else agora, agora,
                         origem, m["confianca"], m.get("anomes") or meses[-1], imp, anterior])
            if atual and atual.get("contestacao_decisao"):  # contestação improcedente registrada: continua na linha
                cur.execute(f"UPDATE verificacao_vendas SET {', '.join(f'{c} = %s' for c in CONTESTACAO)} WHERE id_servico = %s",
                            [atual.get(c) for c in CONTESTACAO] + [m["id_servico"]])
        cur.executemany("INSERT INTO verificacao_vendas_log (id_servico, evento, dados, usuario, em) VALUES (%s, %s, %s, %s, %s)",
                        [[m["id_servico"], origem[:20], json.dumps(m, default=str, ensure_ascii=False), usuario, agora]
                         for m in marcacoes])

        # 3. sugestões dos AnoMes: substitui as anteriores
        cur.execute(f"DELETE FROM cruzamento_sugestoes WHERE origem = %s AND anomes IN ({', '.join(['%s'] * len(meses))})",
                    [origem] + meses)
        cur.executemany("""INSERT INTO cruzamento_sugestoes (origem, anomes, importacao_id, id_venda, id_servico, data_venda,
                           servico_base, confianca, motivo, correcao, penalidade) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        [[origem, s.get("anomes") or meses[-1], imp, s["id_venda"], s["id_servico"], s["data_venda"],
                          (s["servico_base"] or "")[:255], s["confianca"], (s["motivo"] or "")[:255],
                          s.get("correcao"), s.get("penalidade")] for s in sugestoes])
        cur.execute("UPDATE cruzamento_importacoes SET revertidas = %s, manuais_sobrescritas = %s WHERE id = %s",
                    [cont["revertidas"], cont["manuais_sobrescritas"], imp])
    return {"importacao_id": imp, "aplicadas": len(marcacoes), "sugestoes": len(sugestoes), **cont}


def previa_cruzamento(origem, anomes, ids):
    """Quantas marcações uma importação desfaria, quantas verificações manuais sobrescreveria e quantas vendas
    contestadas (que voltaram) ela deixa como estão."""
    ids, meses = set(ids), _meses(anomes)
    atuais = consultar(f"""SELECT id_servico FROM verificacao_vendas
                           WHERE origem = %s AND cruzamento_anomes IN ({', '.join(['%s'] * len(meses))})""", [origem] + meses)
    outras = consultar(f"""SELECT id_servico, status FROM verificacao_vendas
                           WHERE origem <> %s AND id_servico IN ({', '.join(['%s'] * len(ids))})""",
                       [origem] + list(ids)) if ids else []
    return {"revertidas": len({a["id_servico"] for a in atuais} - ids),
            "manuais_sobrescritas": sum(1 for o in outras if o["status"] != "CONTESTADA"),
            "contestadas_mantidas": sum(1 for o in outras if o["status"] == "CONTESTADA")}


def importacoes(origem, limite=10):
    return consultar("""SELECT id, anomes, meses, arquivo, base_de, base_ate, linhas, aplicadas, sugestoes, revertidas,
                               manuais_sobrescritas, nao_existe, usuario, em
                        FROM cruzamento_importacoes WHERE origem = %s ORDER BY id DESC LIMIT %s""", [origem, limite])


def resultado_importacao(imp_id):
    r = consultar("SELECT id, origem, anomes, resultado FROM cruzamento_importacoes WHERE id = %s", [imp_id])
    return (r[0], json.loads(r[0]["resultado"] or "[]")) if r else (None, None)


def sugestoes_periodo(inicio, fim):
    """Sugestões vigentes (da última importação de cada AnoMes) para as vendas do período."""
    return consultar("""SELECT s.origem, s.anomes, s.id_venda, s.id_servico, s.data_venda, s.servico_base, s.confianca, s.motivo,
                               s.correcao, s.penalidade
                        FROM cruzamento_sugestoes s
                        WHERE s.data_venda BETWEEN %s AND %s""", [inicio, fim])
