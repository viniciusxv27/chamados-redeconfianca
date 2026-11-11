# -*- coding: utf-8 -*-
"""Cruzamento da VivoGo (vivogo.vendas_servicos) com a Base Analítica de SVA (Vivo).

O cruzamento parte dos SERVIÇOS SVA lançados na VivoGo (pilar = "SVA") e procura cada um na base, com os
mesmos critérios de confiança da automação SVA (últimos 6 dígitos do documento, DDD + últimos 4 do número,
vendedor e data ±3 dias) e exigindo a MESMA MARCA (NO_SRVC da base × nome do plano na VivoGo):
  1-Exata    documento + número
  2-Forte    documento OU número + vendedor + data
  3-Provável documento + vendedor ou data; número + vendedor ou mesmo dia
Planos móveis/fixa nunca são zerados: as linhas da base de SVA embutido no plano (Controle, Selfie Pós, Fibra)
ficam só como informação. Cada linha da base é usada por um serviço só (um combo pode receber várias).

Ação por serviço SVA da VivoGo:
  APLICAR   na base com Flag 0 e confiança Exata/Forte → vira divergência automática
  SUGESTAO  na base com Flag 0 e confiança Provável → alerta na esteira
  OK        na base com Flag 1 (será paga)
  SEM_BASE  não está na base (base atrasada ou SVA não reconhecido pela Vivo) — só informação
Linhas da base que sobram:
  NAO_EXISTE  SVA avulso / Vivo Total sem serviço SVA correspondente na VivoGo
  PLANO       SVA embutido no plano — não mexe na venda
"""
import datetime as dt
import re
import unicodedata
import warnings
from collections import defaultdict

from . import mysql as db

DIAS = 3  # tolerância entre data da venda (VivoGo) e DT_MVMT (Vivo)
NIVEIS = {3: "1-Exata", 2: "2-Forte", 1: "3-Provável"}
NIVEIS_LONGOS = {3: "1-Exata (documento + número)", 2: "2-Forte (documento ou número + vendedor + data)",
                 1: "3-Provável (documento + data/vendedor, ou número + vendedor/mesmo dia)"}
PENALIDADE = "SVA NÃO ATIVADO"
CORRECAO = "MOVIMENTO INELEGÍVEL"
ORIGEM = "CRUZAMENTO_SVA"

# Marca (NO_SRVC da base) → trechos que aparecem no nome do plano na VivoGo
MARCAS = {
    "NETFLIX": ("netflix",), "AMAZON PRIME": ("amazon", "prime video"), "YOUTUBE PREMIUM": ("youtube",),
    "SPOTIFY": ("spotify",), "VALE SAUDE SEMPRE": ("vale saude", "saude"), "GOOGLE GEMINI": ("gemini", "google ai"),
    "DISNEY PLUS": ("disney",), "GLOBOPLAY": ("globo",), "APPLE MUSIC": ("apple music", "musica"),
    "MAX": ("hbo", "max"), "PREMIERE": ("premiere",),
}
SERVICOS_PLANO = ("Alta", "Troca de Plano", "Migração", "Reativação")

dig = lambda s: re.sub(r"\D", "", str(s or ""))
norm = lambda s: " ".join(unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper().split())
baixo = lambda s: norm(s).lower()


def nivel(d, t, v, dt_ok, dist):
    if d and t:
        return 3
    if (d or t) and v and dt_ok:
        return 2
    # só número (sem documento) colide fácil: exige vendedor ou o mesmo dia
    if (d and (v or dt_ok)) or (t and (v or dist == 0)):
        return 1
    return 0


# ---------------------------------------------------------------- leitura da base
RENOMEAR = {"Documento": "DOCUMENTO", "FL_VNCL": "FLAG_VNCL", "Nome Loja": "NOME_LOJA"}
OBRIGATORIAS = ["AnoMes", "DT_MVMT", "FLAG_VNCL", "DOCUMENTO", "NU_TLFN", "NOME", "DS_SRVC"]


def _txt(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _data(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = _txt(v)[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def ler_base(conteudo):
    """Lê a Base Analítica de SVA (.xlsx exportado da Vivo). A Vivo muda nomes de coluna entre versões."""
    import io
    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    linhas = ws.iter_rows(values_only=True)
    cab = [RENOMEAR.get(_txt(c), _txt(c)) for c in next(linhas)]
    falta = [c for c in OBRIGATORIAS if c not in cab]
    if falta:
        raise ValueError(f"A base não tem as colunas {', '.join(falta)}. Use a Base Analítica de Vendas de SVA "
                         f"com Ativação Vinculada exportada da Vivo.")
    base = []
    for r in linhas:
        reg = {c: r[i] if i < len(r) else None for i, c in enumerate(cab) if c}
        if not re.fullmatch(r"\d{6}", _txt(reg.get("AnoMes"))):
            continue  # rodapé "Filtros aplicados..."
        reg = {k: (_data(v) if k in ("DT_MVMT", "DT_VNCL") else _txt(v)) for k, v in reg.items()}
        if not reg["DT_MVMT"]:
            continue
        reg["VL_TRCO_FACE"] = reg.get("VL_TRCO_FACE") or reg.get("VL_BRTO_PCTO") or "0"
        base.append(reg)
    wb.close()
    if not base:
        raise ValueError("A base não tem nenhuma linha de venda.")
    return base


# ---------------------------------------------------------------- vendas da VivoGo
COLUNAS = """s.`ID_do_serviço` AS id_servico, s.`ID_da_venda` AS id_venda, s.`data_da_venda` AS data_venda,
             s.`PDV` AS pdv, s.`Serviço` AS servico_grupo, s.`Serviço_Técnico` AS servico, s.`Plano_novo` AS plano,
             s.`Nome_do_vendedor` AS vendedor, s.`Nº_acesso` AS acesso, s.`Nº_portabilidade` AS portabilidade,
             s.`CPF_do_cliente` AS cpf, s.`Documento_do_cliente` AS documento, s.`Nome_do_cliente` AS cliente,
             s.`Receita` AS receita, s.`Status_do_Serviço` AS status_servico, s.`pilar` AS pilar"""


def carregar_vendas(inicio, fim):
    lista = db.pdvs() or [""]
    sql = f"""SELECT {COLUNAS} FROM vendas_servicos s
              WHERE s.`PDV` IN ({', '.join(['%s'] * len(lista))}) AND s.`data_da_venda` BETWEEN %s AND %s"""
    return db.consultar(sql, list(lista) + [inicio, fim])


# ---------------------------------------------------------------- casamento
def _embutido(linha):
    clsf = baixo(linha.get("DS_CLSF_SVA"))
    if clsf:
        return not ("avulso" in clsf or "vivo total" in clsf)
    return bool(re.search(r"\d+\s*gb", baixo(linha.get("DS_SRVC")))) or baixo(linha.get("DS_SRVC")).startswith(("controle", "titular"))


def _tem_marca(linha, plano):
    trechos = MARCAS.get(norm(linha.get("NO_SRVC")))
    if not trechos:  # marca desconhecida: usa as palavras do DS_SRVC
        trechos = tuple(p for p in baixo(linha.get("DS_SRVC")).split() if len(p) > 3 and not p.endswith("gb"))[:2]
    p = baixo(plano)
    return any(t in p for t in trechos)


def _combo(servico):
    return "combo" in baixo(servico["plano"])


def _linha_vg(v, idv=None):
    return {
        "VG_ID_da_venda": idv or v["id_venda"], "VG_Data_venda": v["data_venda"], "VG_PDV": v["pdv"],
        "VG_Vendedor": v["vendedor"], "VG_Servico": v["servico_grupo"], "VG_Plano_novo": v["plano"],
        "VG_N_acesso": v["acesso"], "VG_CPF_cliente": v["cpf"], "VG_Nome_cliente": v["cliente"],
        "VG_Receita": float(v["receita"] or 0), "VG_Status_servico": v["status_servico"], "VG_id_servico": v["id_servico"],
    }


def cruzar(base):
    """Devolve a lista de resultados (um por serviço SVA da VivoGo + as linhas da base que sobraram) e o período."""
    datas = [r["DT_MVMT"] for r in base]
    ini, fim = min(datas) - dt.timedelta(days=60), max(datas) + dt.timedelta(days=DIAS)
    anomes = base[0]["AnoMes"]
    mes_ini = dt.date(int(anomes[:4]), int(anomes[4:]), 1)
    todas = carregar_vendas(ini, fim)
    # documento e número valem para a venda inteira (o serviço SVA costuma vir sem o nº de acesso)
    docs_venda, tels_venda = defaultdict(set), defaultdict(set)
    for v in todas:
        docs_venda[v["id_venda"]] |= {dig(v["cpf"])[-6:], dig(v["documento"])[-6:]} - {""}
        tels_venda[v["id_venda"]] |= {(dig(t)[:2], dig(t)[-4:]) for t in (v["acesso"], v["portabilidade"]) if dig(t)}
    vendas = [v for v in todas if v["pilar"] == "SVA"]  # mesmo critério de pilar = 'SVA' (fora pacotes de internet)

    avulsas = [i for i, r in enumerate(base) if not _embutido(r)]
    por_doc, por_tel = defaultdict(set), defaultdict(set)
    for i in avulsas:
        r = base[i]
        r["_doc"] = dig(r["DOCUMENTO"])[-6:]
        r["_tel"] = (_txt(r["NU_TLFN"])[:2], _txt(r["NU_TLFN"])[-4:])
        r["_vend"] = norm(r["NOME"])
        if r["_doc"]:
            por_doc[r["_doc"]].add(i)
        if dig(r["NU_TLFN"]):
            por_tel[r["_tel"]].add(i)

    # todos os pares serviço VivoGo × linha da base com a mesma marca
    pares = []
    for k, v in enumerate(vendas):
        docs, tels = docs_venda[v["id_venda"]], tels_venda[v["id_venda"]]
        cands = set().union(*(por_doc.get(d, set()) for d in docs), *(por_tel.get(t, set()) for t in tels))
        for i in cands:
            r = base[i]
            if not _tem_marca(r, v["plano"]):
                continue
            d, t = r["_doc"] in docs, r["_tel"] in tels
            vend = norm(v["vendedor"]) == r["_vend"]
            dist = abs((v["data_venda"] - r["DT_MVMT"]).days) if v["data_venda"] else 999
            n = nivel(d, t, vend, dist <= DIAS, dist)
            if n:
                pares.append((n, -dist, k, i, d, t, vend, dist <= DIAS))

    # atribuição gulosa: o par mais forte primeiro; cada linha da base vai para um serviço (combo aceita várias)
    pares.sort(key=lambda p: (p[0], p[1]), reverse=True)
    linhas_usadas, por_servico = set(), defaultdict(list)
    for p in pares:
        n, _, k, i = p[:4]
        if i in linhas_usadas or (por_servico[k] and not _combo(vendas[k])):
            continue
        linhas_usadas.add(i)
        por_servico[k].append(p)

    saida = []
    for k, v in enumerate(vendas):
        achados = por_servico.get(k)
        if not achados:
            if mes_ini <= (v["data_venda"] or mes_ini) <= max(datas):
                saida.append({"AnoMes": anomes, "acao": "SEM_BASE", "nivel": 0,
                              "situacao": "SVA lançado na VivoGo que não está na base", **_linha_vg(v)})
            continue
        flag0 = [p for p in achados if base[p[3]].get("FLAG_VNCL") == "0"]
        n, _, _, i, d, t, vend, dt_ok = (flag0 or achados)[0]
        r = base[i]
        acao = ("APLICAR" if n >= 2 else "SUGESTAO") if flag0 else "OK"
        extra = f" · combo: {len(flag0)} de {len(achados)} SVA(s) sem ativação" if len(achados) > 1 else ""
        saida.append({
            **{c: x for c, x in r.items() if not c.startswith("_")},
            "acao": acao, "nivel": n, "confianca": NIVEIS[n], "confianca_longa": NIVEIS_LONGOS[n],
            "situacao": "SINALIZAR - SVA sem ativação vinculada (Flag 0)" if flag0 else "OK - será paga",
            "criterios": " + ".join(x for x, ok in zip(["documento", "número", "vendedor", f"data (±{DIAS}d)"],
                                                       [d, t, vend, dt_ok]) if ok),
            "alvo_id_servico": v["id_servico"], "alvo_motivo": "SVA da mesma marca" + extra,
            **_linha_vg(v),
        })
    for i, r in enumerate(base):
        if i in linhas_usadas:
            continue
        emb = _embutido(r)
        saida.append({**{c: x for c, x in r.items() if not c.startswith("_")},
                      "acao": "PLANO" if emb else "NAO_EXISTE", "nivel": 0,
                      "situacao": "SVA embutido no plano — a venda do plano não é alterada" if emb
                      else "NÃO EXISTE serviço SVA desta marca na VivoGo"})
    return saida, (ini, fim)


def resumo(resultados, n_base=None):
    cont = defaultdict(int)
    for r in resultados:
        cont[r["acao"]] += 1
    datas = [r["DT_MVMT"] for r in resultados if r.get("DT_MVMT")]
    return {"linhas": n_base if n_base is not None else sum(1 for r in resultados if r.get("DT_MVMT")),
            "servicos": sum(cont[a] for a in ("APLICAR", "SUGESTAO", "OK", "SEM_BASE")),
            "aplicar": cont["APLICAR"], "sugestoes": cont["SUGESTAO"], "ok": cont["OK"],
            "sem_base": cont["SEM_BASE"], "nao_existe": cont["NAO_EXISTE"], "plano": cont["PLANO"],
            "anomes": resultados[0]["AnoMes"] if resultados else None,
            "base_de": min(datas).isoformat() if datas else None, "base_ate": max(datas).isoformat() if datas else None}
