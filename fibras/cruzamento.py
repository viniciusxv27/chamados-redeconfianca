"""
Motor da ferramenta "Validação de Vendas Go" (mesma lógica), usado pelo /fibras.

Motor de tratamento e cruzamento: BASE DE VENDAS (arquivo BASE Go)  x
ESTEIRA DE VENDAS VIVO (arquivo ANALITICO DE VENDAS).

Base principal = Base de Vendas. A loja precisa ser sempre a mesma; a data pode
variar 1 dia para mais ou para menos.
Fases do cruzamento (cada ORDEM da Esteira só pode ser usada uma vez):
  0. Ajustes manuais (ORDEM informada pelo usuário)
  1. Protocolo exato, mesma loja, data ±1 dia                                  -> Alta
     (tratamento: remove apóstrofo, símbolos, espaços e "A" no final)
  2. Mesma loja + mesmo dia, protocolo quase igual (até 2 caracteres)         -> Alta
  3. Mesma loja, protocolo quase igual, data ±1 dia                           -> Média
  4. Protocolo exato, mas loja diferente ou data fora da tolerância           -> Muito baixa
  5. Mesma loja + mesmo dia, pareamento pela quantidade de vendas             -> Média / Muito baixa
Protocolo aproximado em outra loja (ou fora da tolerância de data) NÃO é vinculado:
fica "Não localizada" com observação detalhada (loja/data de cada base).
Sobras:  Base de Vendas -> "Não localizada"   |   Esteira Vivo -> "Venda não lançada"
Vendas B2B (protocolo com letras e números + CNPJ no CPF/CNPJ) ficam fora do pareamento.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from rapidfuzz.distance import OSA
from scipy.optimize import linear_sum_assignment

# --------------------------------------------------------------------------- #
# Colunas solicitadas
# --------------------------------------------------------------------------- #
COLUNAS_ANALITICO = [
    "DATA_CRIAÇÃO", "ETAPA INSTALACAO", "STATUS INSTALACAO", "ORDEM", "PRODUTO",
    "ADABAS", "LOGRADOURO", "CEP", "NUMERO", "LOGIN", "NOME VENDEDOR",
    "VENDA COMISSIONADA",
]
# "DATA" não estava na lista original, mas é indispensável para o cruzamento por dia.
COLUNAS_GO = [
    "FILIAL", "SERVIÇO", "Nº DA VENDA", "DATA", "VENDEDOR", "ENDEREÇO", "PLANO",
    "NUMERO PROTOCOLO", "NOVA RECEITA", "|INICIAL| CLASSIFI", "|FINAL| CLASSIFICACAO",
    "COORDENAÇÃO", "REGIÃO", "PILAR", "CPF/CNPJ",
]
COLUNAS_LOJAS = ["ADABAS", "PDV"]

ALTA, MEDIA, MUITO_BAIXA, MANUAL = "Alta", "Média", "Muito baixa", "Manual"
LOCALIZADA, NAO_LOCALIZADA, NAO_LANCADA = "Localizada", "Não localizada", "Venda não lançada"
B2B = "Venda B2B"

_STOPWORDS = {"DE", "DA", "DO", "DAS", "DOS", "E"}


# --------------------------------------------------------------------------- #
# Normalizações
# --------------------------------------------------------------------------- #
def sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c))


def _tokens(texto) -> list[str]:
    texto = sem_acento(texto).upper()
    return [t for t in re.split(r"[^0-9A-Z]+", texto) if t and t not in _STOPWORDS]


def chave_coluna(nome) -> str:
    return "".join(_tokens(nome))


def normalizar_loja(nome) -> str:
    if nome is None or (isinstance(nome, float) and np.isnan(nome)):
        return ""
    return " ".join(_tokens(nome))


def normalizar_pessoa(nome) -> str:
    if nome is None or (isinstance(nome, float) and np.isnan(nome)):
        return ""
    # remove prefixos do tipo "IND-000003 - "
    nome = re.sub(r"^[A-Z]{2,5}-\d+\s*-\s*", "", str(nome).strip().upper())
    return " ".join(_tokens(nome))


def para_texto_id(valor) -> str:
    """Converte códigos (ORDEM, protocolo) em texto sem '.0' e sem espaços."""
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def limpar_protocolo(valor) -> str:
    s = para_texto_id(valor).lstrip("'")
    s = re.sub(r"[^0-9A-Za-z]", "", s).upper()
    return s.rstrip("A")


def formato_protocolo(p: str) -> str:
    if not p:
        return "Vazio"
    if re.fullmatch(r"7\d{9}", p):
        return "Padrão ORDEM"
    if re.fullmatch(r"(1[1-9]|[2-9]\d)9\d{8}", p):
        return "Parece telefone"
    if p.isdigit():
        return f"Numérico fora do padrão ({len(p)} dígitos)"
    return "Alfanumérico (não é ORDEM)"


def eh_cnpj(valor) -> bool:
    return len(re.sub(r"\D", "", para_texto_id(valor))) == 14


def eh_b2b(prot: str, documento) -> bool:
    """Protocolo com letras e números e cliente com CNPJ."""
    return bool(re.search(r"[A-Z]", prot)) and bool(re.search(r"\d", prot)) and eh_cnpj(documento)


# --------------------------------------------------------------------------- #
# Endereço
# --------------------------------------------------------------------------- #
END_CONFERE, END_PARCIAL, END_DIVERGE, END_SEM = (
    "Confere", "Parcialmente divergente", "Divergente", "Sem dados para conferir")
_TIPOS_VIA = {"RUA", "R", "AVENIDA", "AV", "AVN", "TRAVESSA", "TV", "TRAV", "LADEIRA", "LD", "RODOVIA", "ROD",
              "ESTRADA", "EST", "ALAMEDA", "AL", "PRACA", "PC", "BECO", "VIELA", "LARGO", "VIA", "ACESSO",
              "SERVIDAO", "VILA", "CAMINHO", "PASSAGEM", "LOTEAMENTO", "CONDOMINIO", "QUADRA", "PARQUE"}
_TITULOS = {"DR": "DOUTOR", "DRA": "DOUTORA", "PROF": "PROFESSOR", "PROFA": "PROFESSORA", "STO": "SANTO",
            "STA": "SANTA", "SAO": "SAO", "SEN": "SENADOR", "DEP": "DEPUTADO", "PRES": "PRESIDENTE",
            "GOV": "GOVERNADOR", "CEL": "CORONEL", "GAL": "GENERAL", "CAP": "CAPITAO", "MAL": "MARECHAL",
            "PE": "PADRE", "N": "NOSSA", "NSA": "NOSSA", "SRA": "SENHORA"}


def separar_endereco_vendas(texto) -> dict:
    """'Rua X, 485, Bairro Y , CEP 29160-054' -> logradouro, número, bairro e CEP."""
    t = "" if texto is None or (isinstance(texto, float) and np.isnan(texto)) else str(texto).strip()
    cep = re.search(r"CEP\s*([\d.\-\s]{8,10})", t, re.I)
    bairro = re.search(r",\s*Bairro\s*(.*?)\s*(?:,\s*CEP|$)", t, re.I)
    partes = [p.strip() for p in re.split(r",\s*Bairro", t, maxsplit=1, flags=re.I)[0].split(",")]
    numero = partes[1] if len(partes) > 1 else ""
    return dict(logradouro=partes[0] if partes else "", numero=numero,
                bairro=bairro.group(1).strip() if bairro else "",
                cep=re.sub(r"\D", "", cep.group(1)) if cep else "")


def _norm_via(texto) -> str:
    toks = [_TITULOS.get(t, t) for t in _tokens(texto)]
    via = " ".join(t for t in toks if t not in _TIPOS_VIA)
    if not via:  # ruas com nome de uma letra ("RUA E") perdem o nome nas palavras ignoradas
        brutos = re.split(r"[^0-9A-Z]+", sem_acento(texto).upper())
        via = " ".join(t for t in brutos if t and t not in _TIPOS_VIA)
    return via


def _norm_numero(texto) -> str:
    n = re.sub(r"\D", "", para_texto_id(texto)).lstrip("0")
    return n  # "" = sem número (S/N ou 0)


def comparar_endereco(end_vendas, logradouro, numero, cep) -> tuple[str, str]:
    """Compara o endereço da Base de Vendas (texto único) com o da Esteira (colunas
    separadas). Retorna (conferência, detalhe)."""
    v = separar_endereco_vendas(end_vendas)
    e = dict(logradouro=para_texto_id(logradouro), numero=para_texto_id(numero),
             cep=re.sub(r"\D", "", para_texto_id(cep)).zfill(8) if para_texto_id(cep) else "")
    via_v, via_e = _norm_via(v["logradouro"]), _norm_via(e["logradouro"])
    num_v, num_e = _norm_numero(v["numero"]), _norm_numero(e["numero"])
    cep_v, cep_e = v["cep"], e["cep"]

    def fmt_cep(c):
        return f"{c[:5]}-{c[5:]}" if len(c) == 8 else c

    ok, dif, tolerado = [], [], []
    via_ok = False
    if via_v and via_e:
        nota = max(fuzz.token_set_ratio(via_v, via_e), fuzz.ratio(via_v, via_e))
        via_ok = nota >= 85
        (ok if via_ok else dif).append("logradouro")
    num_ok = False
    if num_v and num_e:
        num_ok = num_v == num_e
        # mesma rua e número com 1 dígito trocado/faltando/sobrando: provável erro de digitação
        if not num_ok and via_ok and max(len(num_v), len(num_e)) >= 3 and OSA.distance(num_v, num_e) == 1:
            num_ok = True
            tolerado.append(f"número com 1 dígito diferente (Base de Vendas {v['numero']} | Esteira Vivo {e['numero']})")
        (ok if num_ok else dif).append("número")
    if cep_v and cep_e:
        cep_ok = cep_v == cep_e
        if not cep_ok and via_ok and OSA.distance(cep_v, cep_e) == 1:
            cep_ok = True
            tolerado.append(f"CEP com 1 dígito diferente (Base de Vendas {fmt_cep(cep_v)} | Esteira Vivo {fmt_cep(cep_e)})")
        elif not cep_ok and via_ok and num_ok and cep_v[:3] == cep_e[:3]:
            # mesma rua e mesmo número na mesma cidade: ruas longas têm um CEP por trecho
            cep_ok = True
            tolerado.append(f"CEP diferente para a mesma rua e número (Base de Vendas {fmt_cep(cep_v)} | Esteira Vivo {fmt_cep(cep_e)})")
        (ok if cep_ok else dif).append("CEP")
    if not ok and not dif:
        return END_SEM, ""

    lado_v = f"{v['logradouro']}, {v['numero'] or 'S/N'}, CEP {fmt_cep(cep_v) or '-'}"
    lado_e = f"{e['logradouro']}, {e['numero'] or 'S/N'}, CEP {fmt_cep(cep_e) or '-'}"
    if not dif:
        sem = [c for c, a, b in (("número", num_v, num_e), ("CEP", cep_v, cep_e)) if not (a and b)]
        notas = (["diferença tolerada: " + "; ".join(tolerado)] if tolerado else []) + \
                ([f"sem {' e '.join(sem)} para conferir"] if sem else [])
        return END_CONFERE, "Endereço confere" + (f" ({'; '.join(notas)})" if notas else "")
    nivel = END_DIVERGE if "logradouro" in dif or len(dif) >= 2 else END_PARCIAL
    return nivel, (f"Endereço {nivel.lower()} ({', '.join(dif)} diferente(s)): "
                   f"Base de Vendas {lado_v} | Esteira Vivo {lado_e}")


# --------------------------------------------------------------------------- #
# Seleção das colunas
# --------------------------------------------------------------------------- #
def resolver_colunas(df: pd.DataFrame, desejadas: list[str]) -> tuple[dict, list]:
    """Mapeia o nome solicitado -> nome real da coluna no arquivo (tolera acentos,
    'DE/DA', espaços e nomes truncados)."""
    reais = {chave_coluna(c): c for c in df.columns}
    mapa, faltando = {}, []
    for d in desejadas:
        k = chave_coluna(d)
        if k in reais:
            mapa[d] = reais[k]
            continue
        cand = [c for kk, c in reais.items()
                if len(k) >= 5 and (kk.startswith(k) or k.startswith(kk))]
        if not cand:
            melhor = max(reais, key=lambda kk: fuzz.ratio(k, kk), default=None)
            if melhor is not None and fuzz.ratio(k, melhor) >= 85:
                cand = [reais[melhor]]
        if cand:
            mapa[d] = cand[0]
        else:
            faltando.append(d)
    return mapa, faltando


def tratar_base(df: pd.DataFrame, desejadas: list[str]) -> tuple[pd.DataFrame, dict, list]:
    """Mantém só as colunas pedidas, com os nomes e valores originais."""
    mapa, faltando = resolver_colunas(df, desejadas)
    tratado = df[[mapa[d] for d in desejadas if d in mapa]].copy()
    tratado = tratado.dropna(how="all")
    return tratado, mapa, faltando


# --------------------------------------------------------------------------- #
# Lojas
# --------------------------------------------------------------------------- #
def _nome_loja(nome) -> str:
    """Nome da loja sem a palavra "LOJA": a Base de Vendas grava "MONTSERRAT" e o setor é
    "Loja Montserrat". Com o prefixo, a nota do nome parecido caía abaixo de 85% e 16 das 19
    lojas ficavam sem ADABAS — e sem ADABAS a venda não casa com a Esteira."""
    return " ".join(t for t in normalizar_loja(nome).split() if t != "LOJA")


def sugerir_mapeamento(filiais: list[str], lojas: pd.DataFrame, col_adabas: str,
                       col_pdv: str, salvo: dict | None = None) -> pd.DataFrame:
    """FILIAL (Base de Vendas) -> ADABAS, comparando o nome com o PDV do DADOS LOJAS."""
    salvo = salvo or {}
    lojas = lojas.dropna(subset=[col_adabas, col_pdv])
    pdvs = {_nome_loja(r[col_pdv]): (r[col_pdv], r[col_adabas]) for _, r in lojas.iterrows()}
    linhas = []
    for f in sorted({str(x) for x in filiais if pd.notna(x)}):
        if f in salvo:
            adabas = salvo[f]
            pdv = next((p for p, a in pdvs.values() if a == adabas), "")
            linhas.append({"FILIAL (Base de Vendas)": f, "ADABAS": adabas or "", "PDV": pdv,
                           "Origem": "Salvo pelo usuário"})
            continue
        n = _nome_loja(f)
        if n in pdvs:
            pdv, adabas = pdvs[n]
            origem = "Nome idêntico"
        else:
            melhor = max(pdvs, key=lambda p: fuzz.token_sort_ratio(n, p), default=None)
            nota = fuzz.token_sort_ratio(n, melhor) if melhor else 0
            if nota >= 85:
                pdv, adabas = pdvs[melhor]
                origem = f"Nome parecido ({nota:.0f}%)"
            else:
                pdv, adabas, origem = "", "", "SEM CORRESPONDÊNCIA"
        linhas.append({"FILIAL (Base de Vendas)": f, "ADABAS": adabas, "PDV": pdv, "Origem": origem})
    return pd.DataFrame(linhas)


# --------------------------------------------------------------------------- #
# Classificação de proximidade entre protocolos
# --------------------------------------------------------------------------- #
def distancia(p: str, o: str) -> int:
    """Nº de caracteres diferentes (inclusão, exclusão, troca e inversão de vizinhos)."""
    return OSA.distance(p, o)


def classificar(p: str, o: str, mesmo_vendedor: bool) -> tuple[str, int, str]:
    d = distancia(p, o)
    menor = min(len(p), len(o))
    if d == 0:
        return ALTA, d, "Protocolo idêntico"
    if d <= 2:
        return ALTA, d, f"Protocolo difere em {d} caractere(s)"
    if menor >= 7 and (p in o or o in p):
        return ALTA, d, "Um protocolo contém o outro"
    if len(p) == len(o):
        motivo = "Mesmo tamanho, sequência diferente"
        return MEDIA, d, motivo + (" + mesmo vendedor" if mesmo_vendedor else "")
    if mesmo_vendedor:
        return MEDIA, d, "Protocolo diferente, mas mesmo vendedor"
    return MUITO_BAIXA, d, "Somente loja e dia coincidem"


_PESO = {ALTA: 300, MEDIA: 200, MUITO_BAIXA: 100}


def _pontuacao(nivel: str, p: str, o: str, mesmo_vendedor: bool) -> float:
    sim = OSA.normalized_similarity(p, o) if p and o else 0.0
    return _PESO[nivel] + 50 * sim + (30 if mesmo_vendedor else 0)


def _mesmo_vendedor(a: str, b: str) -> bool:
    return bool(a and b) and fuzz.token_set_ratio(a, b) >= 90


# --------------------------------------------------------------------------- #
# Cruzamento
# --------------------------------------------------------------------------- #
def montar_pedidos(go: pd.DataFrame, mg: dict, mapa_lojas: dict) -> pd.DataFrame:
    """Linhas da Base de Vendas agrupadas em pedidos: combos (BL + TV + Fixo) com o mesmo
    protocolo na mesma loja/dia são uma única ORDEM na Esteira Vivo."""
    g = pd.DataFrame({
        "linha_go": go.index,
        "filial": go[mg["FILIAL"]].astype(str).str.strip(),
        "data": pd.to_datetime(go[mg["DATA"]], errors="coerce", dayfirst=True).dt.normalize(),
        "prot_orig": go[mg["NUMERO PROTOCOLO"]].map(para_texto_id),
        "vendedor": go[mg["VENDEDOR"]].map(normalizar_pessoa),
        "servico": go[mg["SERVIÇO"]].astype(str),
    })
    g["adabas"] = g["filial"].map(lambda f: mapa_lojas.get(f) or "")
    g["prot"] = g["prot_orig"].map(limpar_protocolo)
    doc = go[mg["CPF/CNPJ"]] if "CPF/CNPJ" in mg else pd.Series("", index=go.index)
    g["b2b"] = [eh_b2b(p, d) for p, d in zip(g["prot"], doc)]
    loja_chave = np.where(g["adabas"] != "", g["adabas"], "FILIAL:" + g["filial"])
    prot_chave = np.where(g["prot"] != "", g["prot"], "LINHA" + g["linha_go"].astype(str))
    g["pid"] = loja_chave + "|" + g["data"].dt.strftime("%Y-%m-%d").fillna("") + "|" + prot_chave
    return g


def montar_analitico(an: pd.DataFrame, ma: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "linha_an": an.index,
        "ordem": an[ma["ORDEM"]].map(para_texto_id).map(lambda s: re.sub(r"\D", "", s)),
        "adabas": an[ma["ADABAS"]].astype(str).str.strip(),
        "data": pd.to_datetime(an[ma["DATA_CRIAÇÃO"]], errors="coerce", dayfirst=True).dt.normalize(),
        "vendedor": an[ma["NOME VENDEDOR"]].map(normalizar_pessoa),
    })


def _atribuir(cand: list[tuple], ped_ids: list, ord_ids: list):
    """Atribuição ótima (algoritmo húngaro) maximizando a pontuação.
    cand = [(pid, ordem, pontuacao, info)]"""
    if not cand:
        return []
    pi = {p: i for i, p in enumerate(ped_ids)}
    oi = {o: j for j, o in enumerate(ord_ids)}
    m = np.full((len(ped_ids), len(ord_ids)), -1e9)
    info = {}
    for pid, ordem, pts, inf in cand:
        i, j = pi[pid], oi[ordem]
        if pts > m[i, j]:
            m[i, j] = pts
            info[(i, j)] = inf
    lin, col = linear_sum_assignment(-m)
    return [(ped_ids[i], ord_ids[j], info[(i, j)]) for i, j in zip(lin, col) if m[i, j] > -1e9]


def cruzar(go: pd.DataFrame, mg: dict, an: pd.DataFrame, ma: dict, mapa_lojas: dict,
           escopo_an: pd.Series, janela_dias: int = 1,
           manuais: dict | None = None, rejeitados: set | None = None,
           nomes_lojas: dict | None = None):
    """
    go / an        : Base de Vendas / Esteira de Vendas Vivo tratadas (índice original preservado)
    mg / ma        : mapas coluna solicitada -> coluna real
    mapa_lojas     : FILIAL -> ADABAS
    escopo_an      : máscara booleana (índice de `an`) com as vendas da Esteira a considerar
                     no pareamento e no relatório de "Venda não lançada"
    janela_dias    : diferença de data aceita entre as bases (padrão: 1 dia a mais ou a menos)
    manuais        : {pid: ORDEM} vínculos informados manualmente
    rejeitados     : {(pid, ORDEM)} vínculos que o usuário rejeitou
    nomes_lojas    : {ADABAS: PDV} para as observações

    A loja precisa ser sempre a mesma. Protocolo idêntico com loja ou data fora da
    tolerância vira "Muito baixa"; protocolo aproximado nessa situação não é vinculado
    e recebe uma observação detalhada.
    """
    manuais = manuais or {}
    rejeitados = rejeitados or set()
    nomes_lojas = nomes_lojas or {}
    janela = pd.Timedelta(days=janela_dias)

    linhas = montar_pedidos(go, mg, mapa_lojas)
    ped = (linhas.groupby("pid", sort=False)
           .agg(adabas=("adabas", "first"), filial=("filial", "first"), data=("data", "first"),
                prot=("prot", "first"), vendedor=("vendedor", "first"),
                servicos=("servico", lambda s: " + ".join(sorted(set(s)))),
                b2b=("b2b", "any"),
                qtd_linhas=("linha_go", "size"))
           .reset_index())
    A = montar_analitico(an, ma)
    A["escopo"] = escopo_an.reindex(A["linha_an"]).fillna(False).to_numpy()
    A = A[A["ordem"] != ""].drop_duplicates("ordem").set_index("ordem", drop=False)

    vinc: dict[str, dict] = {}       # pid -> resultado
    usadas: dict[str, str] = {}      # ordem -> pid

    def vincular(pid, ordem, nivel, metodo, motivo, dist):
        vinc[pid] = dict(ordem=ordem, nivel=nivel, metodo=metodo, motivo=motivo, dist=dist)
        usadas[ordem] = pid

    def livres(somente_escopo=True):
        a = A[~A["ordem"].isin(usadas)]
        return a[a["escopo"]] if somente_escopo else a

    def pendentes():
        return ped[~ped["pid"].isin(vinc) & ~ped["b2b"]]

    def divergencias(p, a) -> list[str]:
        """Descrição detalhada das diferenças de loja e data entre as duas bases."""
        txt = []
        if a["adabas"] != p["adabas"]:
            txt.append(f"lojas divergentes: Base de Vendas loja {_rotulo_loja(p['filial'], p['adabas'])}"
                       f" | Esteira Vivo loja {_rotulo_loja(nomes_lojas.get(a['adabas'], ''), a['adabas'])}")
        if pd.notna(p["data"]) and pd.notna(a["data"]):
            dias = int(abs((a["data"] - p["data"]).days))
            if dias > janela_dias:
                txt.append(f"datas divergentes: Base de Vendas {p['data']:%d/%m/%Y} | "
                           f"Esteira Vivo {a['data']:%d/%m/%Y} ({dias} dias; tolerância de {janela_dias})")
        return txt

    def dentro(p, a) -> bool:
        return (a["adabas"] == p["adabas"] and p["adabas"] != "" and pd.notna(p["data"])
                and pd.notna(a["data"]) and abs(a["data"] - p["data"]) <= janela)

    # Fase 0 - manuais
    for pid, ordem in manuais.items():
        ordem = re.sub(r"\D", "", str(ordem))
        if pid in set(ped["pid"]) and ordem in A.index and ordem not in usadas:
            p = ped.loc[ped["pid"] == pid].iloc[0]
            div = divergencias(p, A.loc[ordem])
            motivo = "Vínculo informado pelo usuário" + ("; atenção, " + "; ".join(div) if div else "")
            vincular(pid, ordem, MANUAL, "Ajuste manual", motivo, distancia(p["prot"], ordem))

    # Fase 1 - protocolo exato, mesma loja, data dentro da tolerância
    for _, p in pendentes().iterrows():
        o = p["prot"]
        if o and o in A.index and o not in usadas and (p["pid"], o) not in rejeitados and dentro(p, A.loc[o]):
            dias = int(abs((A.loc[o, "data"] - p["data"]).days))
            motivo = "Protocolo idêntico após tratamento" + (f"; data diverge {dias} dia(s)" if dias else "")
            vincular(p["pid"], o, ALTA, "1. Protocolo exato", motivo, 0)

    # Fase 2 - mesma loja e dia, protocolo quase igual
    _fase_grupo(ped, pendentes, livres, vincular, rejeitados, apenas_alta=True,
                metodo="2. Loja/dia + protocolo aproximado")

    # Fase 3 - mesma loja, protocolo quase igual, data diferente dentro da tolerância
    pend = pendentes()
    pend = pend[(pend["prot"].str.len() >= 7) & (pend["adabas"] != "")]
    liv = livres()
    cand = []
    for _, p in pend.iterrows():
        if pd.isna(p["data"]):
            continue
        perto = liv[(liv["adabas"] == p["adabas"]) & ((liv["data"] - p["data"]).abs() <= janela)]
        for _, a in perto.iterrows():
            dias = int(abs((a["data"] - p["data"]).days))
            d = distancia(p["prot"], a["ordem"])
            if dias == 0 or d > 2 or (p["pid"], a["ordem"]) in rejeitados:
                continue
            mv = _mesmo_vendedor(p["vendedor"], a["vendedor"])
            cand.append((p["pid"], a["ordem"], _pontuacao(ALTA, p["prot"], a["ordem"], mv) - dias,
                         (MEDIA, f"Protocolo difere em {d} caractere(s); data diverge {dias} dia(s)", d)))
    for pid, ordem, (nivel, motivo, d) in _atribuir(cand, list(pend["pid"]), list(liv["ordem"])):
        vincular(pid, ordem, nivel, "3. Mesma loja + protocolo aproximado (data ±1)", motivo, d)

    # Fase 4 - protocolo idêntico, mas loja ou data fora da tolerância -> Muito baixa
    for _, p in pendentes().iterrows():
        o = p["prot"]
        if o and o in A.index and o not in usadas and (p["pid"], o) not in rejeitados:
            div = divergencias(p, A.loc[o])
            vincular(p["pid"], o, MUITO_BAIXA, "4. Protocolo exato com loja/data divergente",
                     "Protocolo idêntico, mas " + "; ".join(div), 0)

    # Fase 5 - pareamento pela quantidade de vendas no dia por loja
    _fase_grupo(ped, pendentes, livres, vincular, rejeitados, apenas_alta=False,
                metodo="5. Quantidade loja/dia")

    # Observação detalhada para quem ficou sem vínculo: protocolo aproximado fora da regra
    aprox = {}
    ordens = list(A.index)
    for _, p in ped[~ped["pid"].isin(vinc) & ~ped["b2b"]].iterrows():
        if len(p["prot"]) < 7:
            continue
        perto = [(d, o) for o in ordens if (d := distancia(p["prot"], o)) <= 2]
        if not perto:
            continue
        d, o = min(perto, key=lambda x: (x[0], A.loc[x[1], "adabas"] != p["adabas"]))
        div = divergencias(p, A.loc[o])
        if o in usadas:
            div.append("ORDEM já vinculada a outra venda da Base de Vendas")
        if not div:
            div = ["fora do escopo da Esteira Vivo selecionado"]
        aprox[p["pid"]] = (f"Número de protocolo aproximado (ORDEM {o}, {d} caractere(s) diferente(s)), mas "
                           + "; ".join(div))

    return _montar_saida(go, an, linhas, ped, A, vinc, usadas, aprox)


def _rotulo_loja(nome: str, adabas: str) -> str:
    nome = str(nome or "").strip()
    if nome and adabas:
        return f"{nome} ({adabas})"
    return nome or adabas or "sem ADABAS"


def _fase_grupo(ped, pendentes, livres, vincular, rejeitados, apenas_alta, metodo):
    pend = pendentes()
    pend = pend[(pend["adabas"] != "") & pend["data"].notna()]
    liv = livres()
    for (adabas, data), gp in pend.groupby(["adabas", "data"]):
        ga = liv[(liv["adabas"] == adabas) & (liv["data"] == data)]
        if ga.empty:
            continue
        cand = []
        for _, p in gp.iterrows():
            for _, a in ga.iterrows():
                if (p["pid"], a["ordem"]) in rejeitados:
                    continue
                mv = _mesmo_vendedor(p["vendedor"], a["vendedor"])
                nivel, d, motivo = classificar(p["prot"], a["ordem"], mv)
                if apenas_alta and nivel != ALTA:
                    continue
                cand.append((p["pid"], a["ordem"], _pontuacao(nivel, p["prot"], a["ordem"], mv),
                             (nivel, motivo, d)))
        for pid, ordem, (nivel, motivo, d) in _atribuir(cand, list(gp["pid"]), list(ga["ordem"])):
            vincular(pid, ordem, nivel, metodo, motivo, d)


def _montar_saida(go, an, linhas, ped, A, vinc, usadas, aprox):
    # Contagens por loja/dia (Base de Vendas x Esteira Vivo no escopo); B2B fica de fora
    qtd_go = ped[~ped["b2b"] | ped["pid"].isin(vinc)].groupby(["adabas", "data"]).size().rename("QTD BASE DE VENDAS (LOJA/DIA)")
    esc = A[A["escopo"]]
    qtd_an = esc.groupby(["adabas", "data"]).size().rename("QTD ESTEIRA VIVO (LOJA/DIA)")

    r = linhas[["linha_go", "pid", "adabas", "data", "prot", "servico", "b2b"]].copy()
    r["PROTOCOLO TRATADO"] = r["prot"]
    r["FORMATO PROTOCOLO"] = r["prot"].map(formato_protocolo)
    r["LOJA ADABAS"] = r["adabas"]
    v = r["pid"].map(vinc)
    r["ORDEM VINCULADA"] = v.map(lambda x: x["ordem"] if isinstance(x, dict) else "")
    r["STATUS CRUZAMENTO"] = np.where(r["ORDEM VINCULADA"] != "", LOCALIZADA,
                                      np.where(r["b2b"], B2B, NAO_LOCALIZADA))
    r["ASSERTIVIDADE"] = v.map(lambda x: x["nivel"] if isinstance(x, dict) else "-")
    r["MÉTODO"] = v.map(lambda x: x["metodo"] if isinstance(x, dict) else "-")
    r["CARACTERES DIFERENTES"] = v.map(lambda x: x["dist"] if isinstance(x, dict) else np.nan)
    r["MOTIVO"] = v.map(lambda x: x["motivo"] if isinstance(x, dict) else "")
    r = r.join(qtd_go, on=["adabas", "data"]).join(qtd_an, on=["adabas", "data"])
    r["QTD ESTEIRA VIVO (LOJA/DIA)"] = r["QTD ESTEIRA VIVO (LOJA/DIA)"].fillna(0).astype(int)
    r["QTD BASE DE VENDAS (LOJA/DIA)"] = r["QTD BASE DE VENDAS (LOJA/DIA)"].fillna(0).astype(int)

    # Repetição de protocolo em combos
    rep = r.groupby("pid")["linha_go"].transform("size")
    r["OBSERVAÇÃO"] = ""
    r.loc[rep > 1, "OBSERVAÇÃO"] = "Combo: protocolo compartilhado por " + rep.astype(str) + " linhas"

    b2b = r["STATUS CRUZAMENTO"] == B2B
    r.loc[b2b, "OBSERVAÇÃO"] = "Venda B2B - protocolo alfanumérico e cliente CNPJ (fora da Esteira de Vendas Vivo)"
    nl = r["STATUS CRUZAMENTO"] == NAO_LOCALIZADA
    ordens_todas = set(A.index)
    obs_nl = []
    for _, x in r[nl].iterrows():
        detalhe = aprox.get(x["pid"], "")
        if detalhe:
            obs_nl.append("Não localizada - " + detalhe)
        elif not x["adabas"]:
            obs_nl.append("Não localizada - loja sem correspondência no DADOS LOJAS")
        elif x["prot"] in ordens_todas and x["prot"] in usadas:
            obs_nl.append("Não localizada - ORDEM já vinculada a outra venda da Base de Vendas")
        elif x["QTD ESTEIRA VIVO (LOJA/DIA)"] == 0:
            obs_nl.append("Não localizada - nenhuma venda da loja neste dia na Esteira de Vendas Vivo")
        else:
            obs_nl.append("Não localizada - loja/dia com mais vendas na Base de Vendas que na Esteira de Vendas Vivo")
    r.loc[nl, "OBSERVAÇÃO"] = (r.loc[nl, "OBSERVAÇÃO"].where(r.loc[nl, "OBSERVAÇÃO"] == "", r.loc[nl, "OBSERVAÇÃO"] + " | ")
                               + pd.Series(obs_nl, index=r.index[nl]))

    # Dados da Esteira Vivo para a ORDEM vinculada
    an_ord = A.set_index("ordem")["linha_an"]
    dados_an = an.add_prefix("ESTEIRA VIVO: ")
    idx_an = r["ORDEM VINCULADA"].map(an_ord)
    extra = dados_an.reindex(idx_an.to_numpy()).reset_index(drop=True)
    extra.index = r.index
    # Etapa e status de instalação em destaque para as vendas localizadas
    for alvo, nome in (("ETAPA INSTALACAO", "ETAPA INSTALAÇÃO"), ("STATUS INSTALACAO", "STATUS INSTALAÇÃO")):
        col = next((c for c in an.columns if chave_coluna(c) == chave_coluna(alvo)), None)
        r[nome] = extra.pop(f"ESTEIRA VIVO: {col}").fillna("") if col else ""

    # Conferência de endereço (só sinaliza; não altera a assertividade)
    def col_por(df, alvo):
        return next((c for c in df.columns if chave_coluna(c).endswith(chave_coluna(alvo))), None)
    c_end = col_por(go, "ENDEREÇO")
    c_log, c_num, c_cep = (col_por(extra, x) for x in ("LOGRADOURO", "NUMERO", "CEP"))
    conf, det = [], []
    for i in r.index:
        if not r.at[i, "ORDEM VINCULADA"] or not c_end or not c_log:
            conf.append("")
            det.append("")
            continue
        a, b = comparar_endereco(go.at[r.at[i, "linha_go"], c_end], extra.at[i, c_log],
                                 extra.at[i, c_num] if c_num else "", extra.at[i, c_cep] if c_cep else "")
        conf.append(a)
        det.append(b)
    r["CONFERÊNCIA ENDEREÇO"] = conf
    r["DETALHE ENDEREÇO"] = det

    cols_cruz = ["LOJA ADABAS", "PROTOCOLO TRATADO", "FORMATO PROTOCOLO", "STATUS CRUZAMENTO",
                 "ASSERTIVIDADE", "ORDEM VINCULADA", "ETAPA INSTALAÇÃO", "STATUS INSTALAÇÃO",
                 "CONFERÊNCIA ENDEREÇO", "DETALHE ENDEREÇO", "MÉTODO", "CARACTERES DIFERENTES", "MOTIVO",
                 "OBSERVAÇÃO", "QTD BASE DE VENDAS (LOJA/DIA)", "QTD ESTEIRA VIVO (LOJA/DIA)"]
    base_go = go.reindex(r["linha_go"]).reset_index(drop=True)
    base_go.index = r.index
    resultado_go = pd.concat([base_go, r[cols_cruz], extra], axis=1)
    resultado_go.insert(0, "ID PEDIDO", r["pid"])

    # Vendas da Esteira Vivo (escopo) que sobraram
    sobras = A[A["escopo"] & ~A["ordem"].isin(usadas)]
    nao_lancadas = an.loc[sobras["linha_an"]].copy()
    nao_lancadas["STATUS CRUZAMENTO"] = NAO_LANCADA
    nao_lancadas["OBSERVAÇÃO"] = NAO_LANCADA

    # Resumo por loja/dia
    res = r.drop_duplicates("pid")
    resumo = (res.assign(n=1).pivot_table(index=["adabas", "data"], columns="ASSERTIVIDADE",
                                          values="n", aggfunc="sum", fill_value=0))
    resumo = resumo.join(qtd_go, how="outer").join(qtd_an, how="outer").fillna(0)
    nl_ = sobras.groupby(["adabas", "data"]).size().rename("VENDA NÃO LANÇADA")
    resumo = resumo.join(nl_, how="outer").fillna(0)
    b2b_ = res[res["STATUS CRUZAMENTO"] == B2B].groupby(["adabas", "data"]).size().rename(B2B)
    resumo = resumo.drop(columns="-", errors="ignore")
    nloc = res[res["STATUS CRUZAMENTO"] == NAO_LOCALIZADA].groupby(["adabas", "data"]).size().rename(NAO_LOCALIZADA)
    resumo = resumo.join(nloc, how="outer").join(b2b_, how="outer").fillna(0)
    end_ = (res[res["CONFERÊNCIA ENDEREÇO"].isin([END_DIVERGE, END_PARCIAL])]
            .groupby(["adabas", "data"]).size().rename("ENDEREÇO DIVERGENTE"))
    resumo = resumo.join(end_, how="outer").fillna(0)
    ordem_cols = ["QTD BASE DE VENDAS (LOJA/DIA)", "QTD ESTEIRA VIVO (LOJA/DIA)", MANUAL, ALTA, MEDIA,
                  MUITO_BAIXA, NAO_LOCALIZADA, B2B, "VENDA NÃO LANÇADA", "ENDEREÇO DIVERGENTE"]
    for c in ordem_cols:
        if c not in resumo:
            resumo[c] = 0
    resumo = resumo[ordem_cols].astype(int).reset_index().rename(columns={"adabas": "ADABAS", "data": "DATA"})

    return resultado_go, nao_lancadas, resumo, ped


def candidatos(pedido: pd.Series, an: pd.DataFrame, ma: dict, usadas: set, janela_dias: int = 1,
               limite: int = 15) -> pd.DataFrame:
    """Sugestões de ORDEM para um pedido (para tratamento manual)."""
    A = montar_analitico(an, ma)
    A = A[A["ordem"] != ""]
    if pd.notna(pedido["data"]):
        A = A[(A["data"] - pedido["data"]).abs() <= pd.Timedelta(days=janela_dias)]
    if A.empty:
        return pd.DataFrame()
    A = A.copy()
    A["MESMA LOJA"] = A["adabas"] == pedido["adabas"]
    A["CARACTERES DIFERENTES"] = A["ordem"].map(lambda o: distancia(pedido["prot"], o))
    A["DIAS DE DIFERENÇA"] = (A["data"] - pedido["data"]).dt.days.abs()
    A["MESMO VENDEDOR"] = A["vendedor"].map(lambda v: _mesmo_vendedor(pedido["vendedor"], v))
    A["JÁ VINCULADA"] = A["ordem"].isin(usadas)
    A = A.sort_values(["MESMA LOJA", "CARACTERES DIFERENTES", "DIAS DE DIFERENÇA", "MESMO VENDEDOR"],
                      ascending=[False, True, True, False]).head(limite)
    return A.rename(columns={"ordem": "ORDEM", "adabas": "ADABAS", "data": "DATA", "vendedor": "VENDEDOR"})[
        ["ORDEM", "ADABAS", "DATA", "VENDEDOR", "MESMA LOJA", "CARACTERES DIFERENTES",
         "DIAS DE DIFERENÇA", "MESMO VENDEDOR", "JÁ VINCULADA"]]
