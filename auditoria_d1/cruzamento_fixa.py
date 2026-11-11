# -*- coding: utf-8 -*-
"""Cruzamento Fixa: vendas de fixa da VivoGo (vivogo.vendas_servicos, pilar Fixa) × ANALÍTICO DE VENDAS (esteira
de instalação da Vivo).

Replica a lógica do projeto "Validacao Vendas Go" (cruzamento.py), trocando o arquivo BASE Go pelo banco:
a base principal são as vendas de fixa da VivoGo, agrupadas em pedidos (combos BL + TV + Fixo com o mesmo
protocolo na mesma loja/dia são uma única ORDEM na Vivo). A loja precisa ser a mesma (PDV → ADABAS); a data
pode variar 1 dia. Cada ORDEM só pode ser usada uma vez:
  1. Protocolo exato, mesma loja, data ±1 dia                              → Alta
     (tratamento: remove apóstrofo, símbolos, espaços e "A" no final)
  2. Mesma loja + mesmo dia, protocolo quase igual (até 2 caracteres)     → Alta
  3. Mesma loja, protocolo quase igual, data ±1 dia                       → Média
  4. Protocolo exato, mas loja diferente ou data fora da tolerância       → Muito baixa
  5. Mesma loja + mesmo dia, pareamento pela quantidade de vendas         → Média / Muito baixa
Sobras: pedido da VivoGo → "Não localizada" | ORDEM da Vivo → "Venda não lançada".
Vendas B2B (protocolo com letras e números + CNPJ) ficam fora do pareamento.
O endereço da VivoGo é conferido com o da Vivo (só sinaliza).

Diferenças da versão original: sem rapidfuzz/scipy (distância OSA e semelhança em Python puro; a atribuição
ótima do algoritmo húngaro vira atribuição gulosa pela maior pontuação — mesma prioridade de regras).

Do resultado do cruzamento para a auditoria (por pedido; só o serviço que a ORDEM indica — SERVICO do Analítico:
FTTH → Alta Banda Larga, TV → Alta TV, voz → Alta Fixo; os outros serviços do combo não são mexidos):
  APLICAR   Localizada com assertividade Alta, CPF conferindo e ETAPA "CANCELADA" → Movimento inelegível · FIBRA CANCELADA
  SUGESTAO  Localizada Média/Muito baixa e cancelada (mesma marcação, confirmar na esteira)
            Localizada instalada mas "VENDA NÃO COMISSIONADA" (movimento inelegível, escolher a penalidade)
            Não localizada na esteira da Vivo (venda inexistente?)
  OK        Localizada, instalada e comissionada
  PENDENTE  Localizada, instalação pendente (entra na próxima base)
  FORA      pedido sem serviço da tecnologia do Analítico (ex.: só TV numa base FTTH) — só informação
  B2B / NAO_LANCADA  só informação. A conferência de endereço e de CPF (6 últimos dígitos) vai para a planilha;
  o endereço da VivoGo quase nunca é o de instalação, por isso não vira alerta.
"""
import datetime as dt
import difflib
import io
import re
import unicodedata
import warnings
from collections import defaultdict

from . import mysql as db

ORIGEM = "CRUZAMENTO_FIXA"
TECNOLOGIAS_BASE = {"FTTH"}  # SERVICO presentes no Analítico importado (definido a cada cruzamento)
CORRECAO = "MOVIMENTO INELEGÍVEL"
PENALIDADE = "FIBRA CANCELADA"
INEXISTENTE = "VENDA INEXISTENTE"
JANELA = 1  # dias de diferença aceitos entre a VivoGo e a Vivo

ALTA, MEDIA, MUITO_BAIXA = "Alta", "Média", "Muito baixa"
LOCALIZADA, NAO_LOCALIZADA, NAO_LANCADA, B2B = "Localizada", "Não localizada", "Venda não lançada", "Venda B2B"
END_CONFERE, END_PARCIAL, END_DIVERGE, END_SEM = "Confere", "Parcialmente divergente", "Divergente", "Sem dados para conferir"

# PDV (VivoGo) → ADABAS (Vivo), da aba ADABÁS do "DADOS LOJAS.xlsx"
ADABAS = {
    "LARANJEIRAS": "ESLA013-001", "MONTSERRAT": "ESD0267-004", "PORTO CANOA": "ESD0267-008",
    "JACARAIPE": "ESD0267-009", "JARDIM CAMBURI": "ESD0267-006", "MASTERPLACE": "ESD0386-001",
    "MARCILIO DE NORONHA": "ESD0267-007", "ITACIBA": "ESD0267-010", "PIUMA": "ESD0267-011",
    "ANCHIETA": "ESD0267-013", "ICONHA": "ESD0267-012", "CENTRO VILA VELHA": "ESD0267-014",
    "NORTE SUL": "ESD0267-015", "SERRA SEDE": "ESD0267-016", "GLORIA": "ESD0267-017", "CENTRO VIX": "ESD0267-018",
    "BOM JESUS DO ITABAPOANA": "RJD1220-002", "QUISSAMA": "RJD1220-003", "SANTO ANTONIO DE PADUA": "RJD1220-004",
    "SAO FIDELIS": "RJD1220-005", "MIRACEMA": "RJD1220-001",
}
_STOP = {"DE", "DA", "DO", "DAS", "DOS", "E"}


# ---------------------------------------------------------------- normalizações (iguais ao projeto original)
def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFKD", str(t)) if not unicodedata.combining(c))


def _tokens(t):
    return [x for x in re.split(r"[^0-9A-Z]+", sem_acento(t or "").upper()) if x and x not in _STOP]


def chave(nome):
    return "".join(_tokens(nome))


def norm_loja(n):
    return " ".join(re.split(r"[^0-9A-Z]+", sem_acento(n or "").upper())).strip()


def adabas_de(pdv):
    return ADABAS.get(" ".join(norm_loja(pdv).split()), "")


def pessoa(n):
    n = re.sub(r"^[A-Z]{2,5}-\d+\s*-\s*", "", str(n or "").strip().upper())
    return " ".join(_tokens(n))


def texto_id(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def limpar_protocolo(v):
    s = texto_id(v).lstrip("'")
    return re.sub(r"[^0-9A-Za-z]", "", s).upper().rstrip("A")


def formato_protocolo(p):
    if not p:
        return "Vazio"
    if re.fullmatch(r"7\d{9}", p):
        return "Padrão ORDEM"
    if re.fullmatch(r"(1[1-9]|[2-9]\d)9\d{8}", p):
        return "Parece telefone"
    if p.isdigit():
        return f"Numérico fora do padrão ({len(p)} dígitos)"
    return "Alfanumérico (não é ORDEM)"


def eh_b2b(prot, documento):
    return bool(re.search(r"[A-Z]", prot)) and bool(re.search(r"\d", prot)) and len(re.sub(r"\D", "", texto_id(documento))) == 14


def osa(a, b):
    """Distância OSA: inclusão, exclusão, troca e inversão de vizinhos."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            c = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio() * 100


def token_set(a, b):
    ta, tb = set(a.split()), set(b.split())
    inter = " ".join(sorted(ta & tb))
    da, dbb = " ".join(sorted(ta - tb)), " ".join(sorted(tb - ta))
    sa, sb = (inter + " " + da).strip(), (inter + " " + dbb).strip()
    if inter and (not da or not dbb):
        return 100.0
    return max(_sim(inter, sa) if inter else 0, _sim(inter, sb) if inter else 0, _sim(sa, sb))


def mesmo_vendedor(a, b):
    return bool(a and b) and token_set(a, b) >= 90


# ---------------------------------------------------------------- endereço
_VIAS = {"RUA", "R", "AVENIDA", "AV", "AVN", "TRAVESSA", "TV", "TRAV", "LADEIRA", "LD", "RODOVIA", "ROD", "ESTRADA",
         "EST", "ALAMEDA", "AL", "PRACA", "PC", "BECO", "VIELA", "LARGO", "VIA", "ACESSO", "SERVIDAO", "VILA", "CAMINHO",
         "PASSAGEM", "LOTEAMENTO", "CONDOMINIO", "QUADRA", "PARQUE"}
_TITULOS = {"DR": "DOUTOR", "DRA": "DOUTORA", "PROF": "PROFESSOR", "PROFA": "PROFESSORA", "STO": "SANTO", "STA": "SANTA",
            "SEN": "SENADOR", "DEP": "DEPUTADO", "PRES": "PRESIDENTE", "GOV": "GOVERNADOR", "CEL": "CORONEL",
            "GAL": "GENERAL", "CAP": "CAPITAO", "MAL": "MARECHAL", "PE": "PADRE", "N": "NOSSA", "NSA": "NOSSA", "SRA": "SENHORA"}


def separar_endereco(t):
    t = str(t or "").strip()
    cep = re.search(r"CEP\s*([\d.\-\s]{8,10})", t, re.I)
    bairro = re.search(r",\s*Bairro\s*(.*?)\s*(?:,\s*CEP|$)", t, re.I)
    partes = [p.strip() for p in re.split(r",\s*Bairro", t, maxsplit=1, flags=re.I)[0].split(",")]
    return dict(logradouro=partes[0] if partes else "", numero=partes[1] if len(partes) > 1 else "",
                bairro=bairro.group(1).strip() if bairro else "", cep=re.sub(r"\D", "", cep.group(1)) if cep else "")


def _via(t):
    toks = [_TITULOS.get(x, x) for x in _tokens(t)]
    via = " ".join(x for x in toks if x not in _VIAS)
    if not via:
        via = " ".join(x for x in re.split(r"[^0-9A-Z]+", sem_acento(t).upper()) if x and x not in _VIAS)
    return via


def _numero(t):
    return re.sub(r"\D", "", texto_id(t)).lstrip("0")


def comparar_endereco(end_vivogo, logradouro, numero, cep):
    v = separar_endereco(end_vivogo)
    e = dict(logradouro=texto_id(logradouro), numero=texto_id(numero),
             cep=re.sub(r"\D", "", texto_id(cep)).zfill(8) if texto_id(cep) else "")
    via_v, via_e = _via(v["logradouro"]), _via(e["logradouro"])
    num_v, num_e = _numero(v["numero"]), _numero(e["numero"])
    cep_v, cep_e = v["cep"], e["cep"]
    fmt = lambda c: f"{c[:5]}-{c[5:]}" if len(c) == 8 else c
    ok, dif, tol = [], [], []
    via_ok = num_ok = False
    if via_v and via_e:
        via_ok = max(token_set(via_v, via_e), _sim(via_v, via_e)) >= 85
        (ok if via_ok else dif).append("logradouro")
    if num_v and num_e:
        num_ok = num_v == num_e
        if not num_ok and via_ok and max(len(num_v), len(num_e)) >= 3 and osa(num_v, num_e) == 1:
            num_ok = True
            tol.append(f"número com 1 dígito diferente (VivoGo {v['numero']} | Vivo {e['numero']})")
        (ok if num_ok else dif).append("número")
    if cep_v and cep_e:
        cep_ok = cep_v == cep_e
        if not cep_ok and via_ok and osa(cep_v, cep_e) == 1:
            cep_ok = True
            tol.append(f"CEP com 1 dígito diferente (VivoGo {fmt(cep_v)} | Vivo {fmt(cep_e)})")
        elif not cep_ok and via_ok and num_ok and cep_v[:3] == cep_e[:3]:
            cep_ok = True
            tol.append(f"CEP diferente para a mesma rua e número (VivoGo {fmt(cep_v)} | Vivo {fmt(cep_e)})")
        (ok if cep_ok else dif).append("CEP")
    if not ok and not dif:
        return END_SEM, ""
    if not dif:
        return END_CONFERE, "Endereço confere" + (f" (diferença tolerada: {'; '.join(tol)})" if tol else "")
    nivel = END_DIVERGE if "logradouro" in dif or len(dif) >= 2 else END_PARCIAL
    lado_v = f"{v['logradouro']}, {v['numero'] or 'S/N'}, CEP {fmt(cep_v) or '-'}"
    lado_e = f"{e['logradouro']}, {e['numero'] or 'S/N'}, CEP {fmt(cep_e) or '-'}"
    return nivel, f"Endereço {nivel.lower()} ({', '.join(dif)} diferente(s)): VivoGo {lado_v} | Vivo {lado_e}"


# ---------------------------------------------------------------- leitura do ANALÍTICO DE VENDAS
CAMPOS = {  # nome interno → nome da coluna no Analítico (comparado sem acento/espaço)
    "anomes": "ANOMES", "data": "DATA_CRIAÇÃO", "etapa": "ETAPA INSTALACAO", "status_inst": "STATUS INSTALACAO",
    "motivo": "MOTIVO", "ordem": "ORDEM", "cpf": "CPF", "produto": "PRODUTO", "adabas": "ADABAS",
    "logradouro": "LOGRADOURO", "cep": "CEP", "numero": "NUMERO", "login": "LOGIN", "vendedor": "NOME VENDEDOR",
    "comissionada": "VENDA COMISSIONADA", "data_agenda": "DATA_AGENDA", "cidade": "CIDADE_CLIENTE",
    "servico": "SERVICO",
}

# SERVICO do Analítico (tecnologia da ORDEM) → Serviço_Técnico da VivoGo que ela indica. Só esse serviço do pedido é
# zerado/sugerido; os outros do combo (TV, fixo) não são mexidos.
SERVICO_INDICADO = {
    "FTTH": ("alta banda larga",), "FIBRA": ("alta banda larga",), "BANDA LARGA": ("alta banda larga",),
    "IPTV": ("alta tv",), "TV": ("alta tv",),
    "VOZ": ("alta fixo", "portabilidade fixa"), "FIXO": ("alta fixo", "portabilidade fixa"), "STFC": ("alta fixo", "portabilidade fixa"),
}


def indicados(servicos, tecnologia):
    alvo = SERVICO_INDICADO.get(norm_loja(tecnologia or "FTTH"), ())
    return [x for x in servicos if (x["servico"] or "").strip().lower() in alvo]
OBRIGATORIAS = ["ordem", "adabas", "data", "etapa"]


def _data(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = texto_id(v)[:10]
    for f in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def ler_base(conteudo):
    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    linhas = ws.iter_rows(values_only=True)
    cab = [chave(c) for c in next(linhas)]
    idx = {}
    for k, nome in CAMPOS.items():
        alvo = chave(nome)
        cand = [i for i, c in enumerate(cab) if c == alvo] or [i for i, c in enumerate(cab) if len(alvo) >= 5 and c.startswith(alvo)]
        if cand:
            idx[k] = cand[0]
    falta = [CAMPOS[k] for k in OBRIGATORIAS if k not in idx]
    if falta:
        raise ValueError(f"A base não tem as colunas {', '.join(falta)}. Use o ANALÍTICO DE VENDAS (esteira de "
                         f"instalação de fixa) exportado da Vivo.")
    base, vistas = [], set()
    for r in linhas:
        reg = {k: (r[i] if i < len(r) else None) for k, i in idx.items()}
        ordem = re.sub(r"\D", "", texto_id(reg["ordem"]))
        data = _data(reg["data"])
        if not ordem or not data or ordem in vistas:  # rodapé "Filtros aplicados..." e ORDEM repetida
            continue
        vistas.add(ordem)
        reg = {k: (_data(v) if k in ("data", "data_agenda") else texto_id(v)) for k, v in reg.items()}
        reg.update(ordem=ordem, data=data, adabas=reg["adabas"].strip(), _vend=pessoa(reg.get("vendedor")))
        base.append(reg)
    wb.close()
    if not base:
        raise ValueError("A base não tem nenhuma venda (coluna ORDEM vazia).")
    return base


# ---------------------------------------------------------------- vendas de fixa da VivoGo
COLUNAS = """s.`ID_do_serviço` AS id_servico, s.`ID_da_venda` AS id_venda, s.`data_da_venda` AS data_venda,
             s.`PDV` AS pdv, s.`Serviço` AS servico_grupo, s.`Serviço_Técnico` AS servico, s.`Plano_novo` AS plano,
             s.`Nome_do_vendedor` AS vendedor, s.`Nº_protocolo` AS protocolo, s.`endereço_do_cliente` AS endereco,
             s.`CPF_do_cliente` AS cpf, s.`Documento_do_cliente` AS documento, s.`Nome_do_cliente` AS cliente,
             s.`Receita` AS receita, s.`Status_do_Serviço` AS status_servico, s.`Status_da_Instalação` AS status_instalacao"""


def carregar_vendas(inicio, fim):
    lista = db.pdvs() or [""]
    sql = f"""SELECT {COLUNAS} FROM vendas_servicos s
              WHERE s.`PDV` IN ({', '.join(['%s'] * len(lista))}) AND s.`data_da_venda` BETWEEN %s AND %s
                AND s.`pilar` = 'Fixa' AND s.`Serviço` IN ('Alta', 'Reativação')"""
    return db.consultar(sql, list(lista) + [inicio, fim])


def montar_pedidos(vendas):
    """Serviços agrupados em pedidos: mesma loja + dia + protocolo tratado."""
    pedidos = {}
    for v in vendas:
        prot = limpar_protocolo(v["protocolo"])
        adabas = adabas_de(v["pdv"])
        loja = adabas or f"FILIAL:{v['pdv']}"
        pid = f"{loja}|{v['data_venda']}|{prot or 'SERV' + str(v['id_servico'])}"
        p = pedidos.setdefault(pid, {"pid": pid, "adabas": adabas, "pdv": v["pdv"], "data": v["data_venda"], "prot": prot,
                                     "prot_orig": texto_id(v["protocolo"]), "vendedor": pessoa(v["vendedor"]),
                                     "servicos": [], "b2b": False})
        p["servicos"].append(v)
        p["b2b"] = p["b2b"] or eh_b2b(prot, v["cpf"] or v["documento"])
    return list(pedidos.values())


# ---------------------------------------------------------------- cruzamento
def classificar(p, o, mv):
    d = osa(p, o)
    if d == 0:
        return ALTA, d, "Protocolo idêntico"
    if d <= 2:
        return ALTA, d, f"Protocolo difere em {d} caractere(s)"
    if min(len(p), len(o)) >= 7 and (p in o or o in p):
        return ALTA, d, "Um protocolo contém o outro"
    if len(p) == len(o):
        return MEDIA, d, "Mesmo tamanho, sequência diferente" + (" + mesmo vendedor" if mv else "")
    if mv:
        return MEDIA, d, "Protocolo diferente, mas mesmo vendedor"
    return MUITO_BAIXA, d, "Somente loja e dia coincidem"


_PESO = {ALTA: 300, MEDIA: 200, MUITO_BAIXA: 100}


def pontuacao(nivel, p, o, mv):
    sim = 1 - osa(p, o) / max(len(p), len(o)) if p and o else 0.0
    return _PESO[nivel] + 50 * sim + (30 if mv else 0)


def _atribuir(cand):
    """cand = [(pid, ordem, pontos, info)] → atribuição 1:1 pela maior pontuação."""
    usados_p, usados_o, saida = set(), set(), []
    for pid, ordem, _, info in sorted(cand, key=lambda c: -c[2]):
        if pid in usados_p or ordem in usados_o:
            continue
        usados_p.add(pid)
        usados_o.add(ordem)
        saida.append((pid, ordem, info))
    return saida


def cruzar(base):
    """Devolve (resultados, período). Um resultado por pedido da VivoGo e um por ORDEM da Vivo sem venda lançada."""
    # no portal o cruzamento roda em várias threads: as tecnologias da base vão por parâmetro, não por global
    tecnologias = {norm_loja(a.get("servico")) or "FTTH" for a in base}
    datas = [a["data"] for a in base]
    de, ate = min(datas), max(datas)
    janela = dt.timedelta(days=JANELA)
    pedidos = montar_pedidos(carregar_vendas(de - janela, ate))
    lojas_base = {a["adabas"] for a in base}
    # escopo: pedidos das lojas que aparecem na base e no período dela (a base da Vivo chega com atraso)
    pedidos = [p for p in pedidos if p["adabas"] in lojas_base and de - janela <= p["data"] <= ate]
    A = {a["ordem"]: a for a in base}
    vinc, usadas = {}, {}

    def vincular(pid, ordem, nivel, metodo, motivo, dist):
        vinc[pid] = dict(ordem=ordem, nivel=nivel, metodo=metodo, motivo=motivo, dist=dist)
        usadas[ordem] = pid

    def pendentes():
        return [p for p in pedidos if p["pid"] not in vinc and not p["b2b"]]

    def livres():
        return [a for o, a in A.items() if o not in usadas]

    def dentro(p, a):
        return a["adabas"] == p["adabas"] and p["adabas"] and abs(a["data"] - p["data"]) <= janela

    def divergencias(p, a):
        txt = []
        if a["adabas"] != p["adabas"]:
            txt.append(f"lojas divergentes: VivoGo {p['pdv']} ({p['adabas'] or 'sem ADABAS'}) | Vivo {a['adabas']}")
        dias = abs((a["data"] - p["data"]).days)
        if dias > JANELA:
            txt.append(f"datas divergentes: VivoGo {p['data']:%d/%m/%Y} | Vivo {a['data']:%d/%m/%Y} ({dias} dias)")
        return txt

    # Fase 1 - protocolo exato, mesma loja, data na tolerância
    for p in pendentes():
        o = p["prot"]
        if o and o in A and o not in usadas and dentro(p, A[o]):
            dias = abs((A[o]["data"] - p["data"]).days)
            vincular(p["pid"], o, ALTA, "1. Protocolo exato",
                     "Protocolo idêntico após tratamento" + (f"; data diverge {dias} dia(s)" if dias else ""), 0)

    def fase_grupo(apenas_alta, metodo):
        grupos = defaultdict(list)
        for p in pendentes():
            if p["adabas"]:
                grupos[(p["adabas"], p["data"])].append(p)
        liv = livres()
        for (adabas, data), gp in grupos.items():
            ga = [a for a in liv if a["adabas"] == adabas and a["data"] == data]
            cand = []
            for p in gp:
                for a in ga:
                    mv = mesmo_vendedor(p["vendedor"], a["_vend"])
                    nivel, d, motivo = classificar(p["prot"], a["ordem"], mv)
                    if apenas_alta and nivel != ALTA:
                        continue
                    cand.append((p["pid"], a["ordem"], pontuacao(nivel, p["prot"], a["ordem"], mv), (nivel, motivo, d)))
            for pid, ordem, (nivel, motivo, d) in _atribuir(cand):
                vincular(pid, ordem, nivel, metodo, motivo, d)

    # Fase 2 - mesma loja e dia, protocolo quase igual
    fase_grupo(True, "2. Loja/dia + protocolo aproximado")

    # Fase 3 - mesma loja, protocolo quase igual, data ±1
    cand = []
    liv = livres()
    for p in pendentes():
        if len(p["prot"]) < 7 or not p["adabas"]:
            continue
        for a in liv:
            if a["adabas"] != p["adabas"] or abs(a["data"] - p["data"]) > janela:
                continue
            dias, d = abs((a["data"] - p["data"]).days), osa(p["prot"], a["ordem"])
            if dias == 0 or d > 2:
                continue
            mv = mesmo_vendedor(p["vendedor"], a["_vend"])
            cand.append((p["pid"], a["ordem"], pontuacao(ALTA, p["prot"], a["ordem"], mv) - dias,
                         (MEDIA, f"Protocolo difere em {d} caractere(s); data diverge {dias} dia(s)", d)))
    for pid, ordem, (nivel, motivo, d) in _atribuir(cand):
        vincular(pid, ordem, nivel, "3. Mesma loja + protocolo aproximado (data ±1)", motivo, d)

    # Fase 4 - protocolo idêntico, mas loja ou data fora da tolerância
    for p in pendentes():
        o = p["prot"]
        if o and o in A and o not in usadas:
            vincular(p["pid"], o, MUITO_BAIXA, "4. Protocolo exato com loja/data divergente",
                     "Protocolo idêntico, mas " + "; ".join(divergencias(p, A[o]) or ["fora da tolerância"]), 0)

    # Fase 5 - pareamento pela quantidade de vendas no dia por loja
    fase_grupo(False, "5. Quantidade loja/dia")

    # Observação para quem ficou sem par
    qtd_vivo = defaultdict(int)
    for a in base:
        qtd_vivo[(a["adabas"], a["data"])] += 1
    saida = []
    for p in pedidos:
        if p["pid"] not in vinc and p["data"] < de:
            continue  # venda de antes do início da base sem par: é da base anterior
        saida.append(_resultado_pedido(p, vinc.get(p["pid"]), A, usadas, qtd_vivo, divergencias, tecnologias))
    for o, a in A.items():
        if o not in usadas:
            saida.append({"acao": "NAO_LANCADA", "status_cruzamento": NAO_LANCADA, "ordem": o, "adabas": a["adabas"],
                          "data_vivo": a["data"], "etapa": a.get("etapa"), "status_inst": a.get("status_inst"),
                          "produto": a.get("produto"), "vendedor_vivo": a.get("vendedor"),
                          "observacao": "ORDEM da Vivo sem venda de fixa lançada na VivoGo"})
    return saida, (de, ate)


def _situacao_vivo(a):
    etapa = (a.get("etapa") or "").upper()
    if "CANCEL" in etapa:
        return "CANCELADA"
    if "INSTALADA" in etapa:
        return "INSTALADA"
    if "PEND" in etapa:
        return "PENDENTE"
    return etapa or "-"


def _resultado_pedido(p, v, A, usadas, qtd_vivo, divergencias, tecnologias=None):
    tecnologias = tecnologias or TECNOLOGIAS_BASE
    s0 = p["servicos"][0]
    r = {"pid": p["pid"], "VG_ID_da_venda": s0["id_venda"], "VG_Data_venda": p["data"], "VG_PDV": p["pdv"],
         "adabas": p["adabas"], "VG_Vendedor": s0["vendedor"], "VG_Nome_cliente": s0["cliente"],
         "VG_Protocolo": p["prot_orig"], "protocolo_tratado": p["prot"], "formato_protocolo": formato_protocolo(p["prot"]),
         "VG_Servicos": " + ".join(sorted({x["servico"] or x["servico_grupo"] for x in p["servicos"]})),
         "VG_Plano_novo": s0["plano"], "VG_Receita": sum(float(x["receita"] or 0) for x in p["servicos"]),
         "VG_Endereco": s0["endereco"], "ids_servico": [x["id_servico"] for x in p["servicos"]], "_servicos": p["servicos"],
         "VG_Status_servico": s0["status_servico"], "qtd_servicos": len(p["servicos"])}
    if p["b2b"]:
        return {**r, "acao": "B2B", "status_cruzamento": B2B, "assertividade": "-",
                "observacao": "Venda B2B - protocolo alfanumérico e cliente CNPJ (fora da esteira da Vivo)"}
    if not v:
        alvo = [x for t in tecnologias for x in indicados(p["servicos"], t)]
        if not alvo:
            return {**r, "acao": "FORA", "status_cruzamento": NAO_LOCALIZADA, "assertividade": "-",
                    "observacao": f"Não localizada - serviço não acompanhado pelo Analítico ({', '.join(sorted(tecnologias))})"}
        r.update(alvo_ids=[x["id_servico"] for x in alvo], VG_Servico_alvo=" + ".join(x["servico"] for x in alvo))
        if len(p["prot"]) >= 7:
            perto = sorted(((osa(p["prot"], o), A[o]["adabas"] != p["adabas"], o) for o in A), key=lambda x: (x[0], x[1]))
            if perto and perto[0][0] <= 2:
                d, _, o = perto[0]
                div = divergencias(p, A[o])
                if o in usadas:
                    div.append("ORDEM já vinculada a outra venda da VivoGo")
                obs = (f"Número de protocolo aproximado (ORDEM {o}, {d} caractere(s) diferente(s)), mas "
                       + "; ".join(div or ["fora do escopo da base"]))
                return {**r, "acao": "SUGESTAO", "status_cruzamento": NAO_LOCALIZADA, "assertividade": "-",
                        "correcao": INEXISTENTE, "observacao": "Não localizada - " + obs}
        if p["prot"] in A and p["prot"] in usadas:
            obs = "ORDEM já vinculada a outra venda da VivoGo"
        elif not qtd_vivo.get((p["adabas"], p["data"])):
            obs = "nenhuma venda da loja neste dia na esteira da Vivo"
        else:
            obs = "loja/dia com mais vendas na VivoGo que na esteira da Vivo"
        return {**r, "acao": "SUGESTAO", "status_cruzamento": NAO_LOCALIZADA, "assertividade": "-",
                "correcao": INEXISTENTE, "observacao": f"Não localizada - {obs} (protocolo {p['prot_orig'] or 'vazio'})"}

    a = A[v["ordem"]]
    sit = _situacao_vivo(a)
    cpf_vivo = re.sub(r"\D", "", a.get("cpf") or "")[-6:]
    docs = {re.sub(r"\D", "", x["cpf"] or "")[-6:] for x in p["servicos"]} | {re.sub(r"\D", "", x["documento"] or "")[-6:] for x in p["servicos"]}
    cpf_ok = None if len(cpf_vivo) < 6 else cpf_vivo in docs
    r["cpf_confere"] = "-" if cpf_ok is None else "Sim" if cpf_ok else "Não"
    end, end_det = comparar_endereco(s0["endereco"], a.get("logradouro"), a.get("numero"), a.get("cep"))
    r.update({"status_cruzamento": LOCALIZADA, "assertividade": v["nivel"], "metodo": v["metodo"], "motivo_vinculo": v["motivo"],
              "ordem": v["ordem"], "data_vivo": a["data"], "etapa": a.get("etapa"), "status_inst": a.get("status_inst"),
              "motivo_vivo": a.get("motivo"), "produto": a.get("produto"), "vendedor_vivo": a.get("vendedor"),
              "comissionada": a.get("comissionada"), "endereco": end, "endereco_detalhe": end_det, "situacao_vivo": sit})
    vivo = f"ORDEM {v['ordem']} · {a.get('status_inst') or a.get('etapa')}" + (f" ({a['motivo']})" if a.get("motivo") else "")
    if cpf_ok is False:
        vivo += " · CPF da Vivo diferente do cliente da VivoGo"
    alvo = indicados(p["servicos"], a.get("servico"))
    r.update(servico_vivo=a.get("servico"), alvo_ids=[x["id_servico"] for x in alvo],
             VG_Servico_alvo=" + ".join(x["servico"] for x in alvo))
    if not alvo and (sit == "CANCELADA" or "NAO COMISSIONADA" in sem_acento(a.get("comissionada") or "").upper()):
        return {**r, "acao": "SUGESTAO", "correcao": CORRECAO, "penalidade": PENALIDADE if sit == "CANCELADA" else None,
                "alvo_ids": [x["id_servico"] for x in p["servicos"]],
                "observacao": f"{'Fibra cancelada' if sit == 'CANCELADA' else 'Não comissionada'} na Vivo ({vivo}), mas o serviço "
                              f"indicado ({a.get('servico') or 'FTTH'}) não está lançado neste pedido"}
    if sit == "CANCELADA":
        # automático só com vínculo de assertividade Alta e o CPF conferindo; o resto vai para a esteira confirmar
        acao = "APLICAR" if v["nivel"] == ALTA and cpf_ok is not False else "SUGESTAO"
        return {**r, "acao": acao, "correcao": CORRECAO, "penalidade": PENALIDADE,
                "observacao": f"Fibra cancelada na Vivo: {vivo}"}
    if "NAO COMISSIONADA" in sem_acento(a.get("comissionada") or "").upper():
        return {**r, "acao": "SUGESTAO", "correcao": CORRECAO, "observacao": f"Venda não comissionada pela Vivo: {vivo}"}
    if sit == "PENDENTE":
        return {**r, "acao": "PENDENTE", "observacao": f"Instalação pendente: {vivo}"}
    return {**r, "acao": "OK", "observacao": f"Instalada: {vivo}"}


def resumo(resultados, base):
    cont = defaultdict(int)
    serv = defaultdict(int)
    for r in resultados:
        cont[r["acao"]] += 1
        serv[r["acao"]] += len(r.get("alvo_ids") or [])
    loc = [r for r in resultados if r.get("status_cruzamento") == LOCALIZADA]
    datas = [a["data"] for a in base]
    meses = sorted({a["anomes"] for a in base if re.fullmatch(r"\d{6}", a.get("anomes") or "")}
                   or {d.strftime("%Y%m") for d in datas})
    return {"linhas": len(base), "pedidos": sum(1 for r in resultados if r["acao"] != "NAO_LANCADA"),
            "aplicar": serv["APLICAR"], "aplicar_pedidos": cont["APLICAR"], "sugestoes": cont["SUGESTAO"],
            "ok": cont["OK"], "pendentes": cont["PENDENTE"], "b2b": cont["B2B"], "nao_lancadas": cont["NAO_LANCADA"],
            "fora": cont["FORA"],
            "nao_localizadas": sum(1 for r in resultados if r.get("status_cruzamento") == NAO_LOCALIZADA and r["acao"] != "FORA"),
            "nao_existe": sum(1 for r in resultados if r.get("status_cruzamento") == NAO_LOCALIZADA and r["acao"] != "FORA"),
            "localizadas": len(loc), "alta": sum(1 for r in loc if r["assertividade"] == ALTA),
            "media": sum(1 for r in loc if r["assertividade"] == MEDIA),
            "muito_baixa": sum(1 for r in loc if r["assertividade"] == MUITO_BAIXA),
            "end_diverge": sum(1 for r in loc if r.get("endereco") in (END_DIVERGE, END_PARCIAL)),
            "meses": meses, "anomes": meses[-1] if meses else None,
            "base_de": min(datas).isoformat(), "base_ate": max(datas).isoformat()}
