# -*- coding: utf-8 -*-
"""Regras da esteira D-1 (porte do app.py do sistema "Auditoria D-1", sem o Flask).

Pontos de atenção automáticos, montagem do registro de verificação (ação/receita validada), índice do mês
(em cache), sugestões dos cruzamentos e as planilhas .xlsx. As views só cuidam de HTTP e permissão.
"""
import calendar
import datetime as dt
import io
import json
import pickle
import re
import threading
import unicodedata
import zlib
from collections import defaultdict
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from . import categorias, cruzamento_fixa, cruzamento_sva, formato
from . import mysql as db

digitos = lambda s: re.sub(r"\D", "", str(s or ""))  # noqa: E731


# ---------------------------------------------------------------- util
def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    s = str(v).strip().replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"Valor inválido: {v}")


def _txt(v, n=None):
    s = str(v or "").strip()
    return (s[:n] if n else s) or None


def _iso(v):
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return v


def hoje():
    return timezone.localdate()


def mes(param):
    """'2026-10' → (primeiro dia, último dia). Sem parâmetro: mês atual."""
    h = hoje()
    try:
        a, m = map(int, (param or "").split("-"))
        dt.date(a, m, 1)
    except ValueError:
        a, m = h.year, h.month
    return dt.date(a, m, 1), dt.date(a, m, calendar.monthrange(a, m)[1])


def doc_valido(doc):
    d = digitos(doc)
    if len(d) == 11:
        if d == d[0] * 11:
            return False
        for n in (9, 10):
            s = sum(int(d[i]) * (n + 1 - i) for i in range(n))
            if (s * 10 % 11) % 10 != int(d[n]):
                return False
        return True
    if len(d) == 14:
        if d == d[0] * 14:
            return False
        for n, pesos in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]), (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
            r = sum(int(d[i]) * pesos[i] for i in range(n)) % 11
            if (0 if r < 2 else 11 - r) != int(d[n]):
                return False
        return True
    return False


def numero_suspeito(acesso):
    d = digitos(acesso)
    if len(d) < 10:
        return bool(d)
    local = d[2:]
    return bool(re.search(r"(\d)\1{5,}", local)) or "1234567" in local or "9876543" in local


def documento_suspeito(doc):
    """Padrão de documento inventado: todos os dígitos iguais ou sequência (123456789..., 987654321...)."""
    d = digitos(doc)
    if not d:
        return False
    base = d[:9] if len(d) == 11 else d[:12] if len(d) == 14 else d
    return len(set(base)) <= 2 or "123456789" in d or "987654321" in d or "012345678" in d


def doc_cliente(r):
    return digitos(r.get("cpf")) or digitos(r.get("documento"))


def _nome(n):
    n = unicodedata.normalize("NFKD", str(n or "")).encode("ascii", "ignore").decode()
    return " ".join(n.upper().split())


def _mesmo_cliente(a, b):
    """Nomes do mesmo cliente: iguais ou com o mesmo primeiro e último nome (abreviações e 'DA/DE' não contam)."""
    a, b = _nome(a).split(), _nome(b).split()
    if not a or not b:
        return True
    return a == b or (a[0] == b[0] and a[-1] == b[-1])


def chave_duplicidade(o):
    """Venda em duplicidade = o mesmo lançamento repetido: mesmo dia, mesmo cliente, mesmo serviço, plano, receita e
    nº de acesso (sem acesso, só dentro da mesma venda). Mesmo acesso em outra venda não é duplicidade: o cliente pode
    ter comprado de novo."""
    acesso = digitos(o["acesso"])
    return (_iso(o["data_venda"]), doc_cliente(o), (o["servico"] or "").strip().upper(),
            (o["plano"] or "").strip().upper(), str(_num(o["receita"])), acesso or f"venda:{o['id_venda']}")


# ---------------------------------------------------------------- alertas
def montar_alertas(r, idx):
    a = []
    if (r["status_servico"] or "").lower() == "cancelado":
        a.append({"nivel": "alto", "txt": "Serviço cancelado na operadora", "sugere": "VENDA INEXISTENTE"})
    if (r["status_bko"] or "").lower() == "improcedente":
        a.append({"nivel": "alto", "txt": "BKO marcou como improcedente"})

    # documento
    cpf, doc = digitos(r["cpf"]), digitos(r["documento"])
    if cpf and doc and cpf != doc:
        a.append({"nivel": "medio", "txt": "CPF do cliente diferente do documento", "sugere": "DIVERGÊNCIA CADASTRAL"})
    d = cpf or doc
    if d and documento_suspeito(d):
        a.append({"nivel": "alto", "txt": "Documento fake: dígitos repetidos ou em sequência",
                  "sugere": "DIVERGÊNCIA CADASTRAL"})
    elif d and not doc_valido(d):
        a.append({"nivel": "medio", "txt": "Documento fake: CPF/CNPJ com dígito verificador inválido",
                  "sugere": "DIVERGÊNCIA CADASTRAL"})
    if d:
        outros = sorted({n for n in idx["nomes_doc"].get(d, ()) if not _mesmo_cliente(n, r["cliente"])})
        if outros:
            a.append({"nivel": "medio", "txt": f"Documento lançado com outro nome no mês: {', '.join(outros[:3])}"
                      " (possível documento fake)", "sugere": "DIVERGÊNCIA CADASTRAL"})

    # número de acesso
    if r["acesso"] and numero_suspeito(r["acesso"]):
        a.append({"nivel": "medio", "txt": "Número de acesso suspeito (possível número fake)", "sugere": "LINHA INCORRETA"})
    outras = [o for o in idx["acessos"].get(digitos(r["acesso"]), [])
              if o["id_venda"] != r["id_venda"] and doc_cliente(o) != d]
    if outras:
        vendas = sorted({(o["id_venda"], _iso(o["data_venda"])) for o in outras}, key=lambda x: x[1])
        a.append({"nivel": "medio", "txt": f"Número de acesso lançado para outro cliente em {len(vendas)} venda(s) no mês"
                  " (possível número fake)", "vendas": [{"id_venda": v, "data": dd} for v, dd in vendas],
                  "sugere": "LINHA INCORRETA"})

    # duplicidade
    dup = idx["duplicados"].get(r["id_servico"])
    if dup:
        if dup["original"] == r["id_servico"]:
            a.append({"nivel": "baixo", "txt": f"Lançamento repetido em {len(dup['copias'])} outro(s) serviço(s) "
                      "(este é o 1º, que vale)", "vendas": dup["vendas"]})
        else:
            a.append({"nivel": "alto", "txt": "Venda em duplicidade: mesmo dia, cliente, serviço, plano, receita e acesso "
                      f"do serviço {dup['original']}", "vendas": dup["vendas"], "sugere": "VENDA EM DUPLICIDADE"})

    if not r["acesso"]:
        a.append({"nivel": "baixo", "txt": "Venda sem número de acesso"})
    if r["receita"] is not None and float(r["receita"]) == 0:
        a.append({"nivel": "baixo", "txt": "Receita zerada"})
    return a


def serializar(r, idx):
    v = None
    if r["v_status"]:
        v = {"status": r["v_status"], "correcao": r["v_correcao"], "penalidade": r["v_penalidade"],
             "acao": r["v_acao"], "receita_correta": _num(r["v_receita_correta"]),
             "plano_correto": r["v_plano_correto"], "linha_correta": r["v_linha_correta"],
             "documento_correto": r["v_documento_correto"], "receita_validada": _num(r["v_receita_validada"]),
             "alterar_vivogo": bool(r["v_alterar_vivogo"]), "observacao": r["v_observacao"],
             "por": r["v_por"], "em": _iso(r["v_em"]), "origem": r.get("v_origem") or "MANUAL",
             "confianca": r.get("v_confianca"), "anomes": r.get("v_anomes"),
             "receita_original": _num(r.get("v_receita_original"))}
        if r.get("v_contestacao_decisao"):
            v["contestacao"] = {"decisao": r["v_contestacao_decisao"], "solicitante": r["v_contestacao_solicitante"],
                                "motivo": r["v_contestacao_motivo"], "por": r["v_contestacao_por"],
                                "em": _iso(r["v_contestacao_em"])}
        if r.get("v_divergencia_contestada"):
            try:
                d = json.loads(r["v_divergencia_contestada"])
                v["divergencia_contestada"] = {"correcao": d.get("correcao_vivogo"), "penalidade": d.get("tipo_penalidade"),
                                               "receita_validada": _num(d.get("receita_validada")),
                                               "origem": d.get("origem"), "por": d.get("verificado_por")}
            except ValueError:
                pass
    base = {k: (_num(val) if isinstance(val, Decimal) else _iso(val)) for k, val in r.items() if not k.startswith("v_")}
    base["alertas"] = montar_alertas(r, idx)
    base["verificacao"] = v
    return base


# ---------------------------------------------------------------- índice do mês (cache do Django)
# As linhas do mês ficam no cache compartilhado (Redis), comprimidas; o índice montado a partir delas fica na
# memória do processo enquanto o carimbo do cache não mudar (montar é rápido; buscar no MySQL é que pesa).
CAMPOS_INDICE = ("id_servico", "id_venda", "data_venda", "acesso", "plano", "servico", "receita", "cpf", "documento",
                 "cliente")
_memo = {}
_trava_memo = threading.Lock()


def _chave_indice(inicio):
    return f"auditoria_d1:indice:{inicio.isoformat()}"


def _montar_indice(linhas):
    acessos, nomes_doc, grupos = defaultdict(list), defaultdict(set), defaultdict(list)
    for o in linhas:
        d = digitos(o["acesso"])
        if d:
            acessos[d].append(o)
        if doc_cliente(o) and _nome(o["cliente"]):
            nomes_doc[doc_cliente(o)].add(_nome(o["cliente"]))
        grupos[chave_duplicidade(o)].append(o)
    duplicados = {}
    for g in grupos.values():
        if len(g) < 2:
            continue
        g.sort(key=lambda o: (int(digitos(o["id_venda"]) or 0), int(digitos(o["id_servico"]) or 0)))
        info = {"original": g[0]["id_servico"], "copias": [o["id_servico"] for o in g[1:]],
                "vendas": [{"id_venda": o["id_venda"], "data": _iso(o["data_venda"]), "servico": o["id_servico"]}
                           for o in g]}
        for o in g:
            duplicados[o["id_servico"]] = info
    return {"linhas": linhas, "acessos": acessos, "nomes_doc": nomes_doc, "duplicados": duplicados}


def indice_mes(inicio, fim):
    """{'linhas': [...], 'acessos': {dígitos: [linhas]}, ...} do mês, em cache (5 min no mês atual, 30 min nos
    anteriores). Nada é pré-carregado: o primeiro acesso do mês monta o cache."""
    chave = _chave_indice(inicio)
    ttl = 300 if inicio <= hoje() <= fim else 1800
    blob = cache.get(chave)
    if blob:
        carimbo = blob[0]
        with _trava_memo:
            m = _memo.get(chave)
            if m and m[0] == carimbo:
                return m[1]
        try:
            tuplas = pickle.loads(zlib.decompress(blob[1]))
            linhas = [dict(zip(CAMPOS_INDICE, t)) for t in tuplas]
        except Exception:  # noqa: BLE001 — cache corrompido: lê de novo
            blob = None
    if not blob:
        linhas = [{c: r.get(c) for c in CAMPOS_INDICE} for r in db.indice_mes(inicio, fim)]
        carimbo = timezone.now().isoformat()
        tuplas = [tuple(o[c] for c in CAMPOS_INDICE) for o in linhas]
        cache.set(chave, (carimbo, zlib.compress(pickle.dumps(tuplas, protocol=4))), ttl)
    idx = _montar_indice(linhas)
    with _trava_memo:
        for k in [k for k in _memo if k != chave and len(_memo) > 3]:
            _memo.pop(k, None)
        _memo[chave] = (carimbo, idx)
    return idx


def resumo_dias(inicio, fim):
    status = {v["id_servico"]: v["status"] for v in db.verificacoes_mes(inicio, fim)}
    resumo = defaultdict(lambda: {"total": 0, "verificadas": 0, "divergentes": 0})
    for l in indice_mes(inicio, fim)["linhas"]:  # noqa: E741
        r = resumo[_iso(l["data_venda"])]
        st = status.get(l["id_servico"])
        r["total"] += 1
        r["verificadas"] += st is not None
        r["divergentes"] += st == "DIVERGENTE"
    return [{"dia": d, **r} for d, r in sorted(resumo.items())]


def vendas_do_dia(dia):
    inicio, fim = mes(dia.strftime("%Y-%m"))
    idx = indice_mes(inicio, fim)
    itens = [serializar(r, idx) for r in db.vendas_periodo(dia, dia)]
    alertas_cruzamento(itens, db.sugestoes_periodo(dia, dia))
    return itens


# ---------------------------------------------------------------- busca geral
LIMITE_BUSCA = 200


def _primeiro_do_mes(d, meses_antes):
    a, m = divmod(d.year * 12 + d.month - 1 - meses_antes, 12)
    return dt.date(a, m + 1, 1)


def tipo_busca(termo):
    """('id', dígitos) para nº da venda/serviço, ('doc', dígitos) para CPF (11) ou CNPJ (14). Com ou sem máscara."""
    termo = str(termo or "").strip()
    d = digitos(termo)
    if re.search(r"[^\d\s.\-/#()]", termo) or not d:
        raise ValueError("Digite o nº da venda (ou do serviço) ou o CPF/CNPJ do cliente.")
    if len(d) in (11, 14):
        return "doc", d
    if 3 <= len(d) <= 10:
        return "id", d
    raise ValueError("Nº da venda com 3 a 10 dígitos, CPF com 11 ou CNPJ com 14.")


def _item_busca(r):
    return {"id_servico": r["id_servico"], "id_venda": r["id_venda"], "data_venda": _iso(r["data_venda"]),
            "pdv": r["pdv"], "servico": r["servico"], "pilar": r["pilar"], "cliente": r["cliente"],
            "documento": formato.doc_vivogo(r["cpf"] or r["documento"]), "plano": r["plano"],
            "receita": _num(r["receita"]), "status": r["v_status"] or "PENDENTE", "correcao": r["v_correcao"],
            "origem": r["v_origem"], "contestacao": r["v_contestacao_decisao"]}


def buscar(termo, ampliar=False):
    """Busca geral da esteira, em etapas para não varrer a tabela de origem (ver db.buscar_vendas):
    1. nº de venda já verificada: acha a data pela verificacao_vendas (qualquer época) e lê só aquele dia;
    2. últimos 3 meses (mês atual + 2 anteriores);
    3. sem resultado (ou ampliar=True): até 12 meses.
    A etapa 1 não encerra a busca: os números de venda e de serviço se sobrepõem, então o mesmo nº pode ser uma venda
    verificada e o serviço (pendente) de outra venda. O que ela acha se soma aos 3 meses; não amplia sozinho para
    12 meses (custaria ~2 s a mais em toda busca de venda verificada), mas oferece o botão de ampliar.
    Devolve {itens, tipo, de, ate, truncado, pode_ampliar, fonte}."""
    tipo, valor = tipo_busca(termo)
    h = hoje()
    fim = mes(h.strftime("%Y-%m"))[1]
    ini3, ini12 = _primeiro_do_mes(h, 2), _primeiro_do_mes(h, 11)
    verificadas = []
    if tipo == "id":
        datas = db.datas_verificadas(valor)
        verificadas = db.buscar_vendas(tipo, valor, datas=datas, limite=LIMITE_BUSCA) if datas else []
    if ampliar:
        linhas, de = db.buscar_vendas(tipo, valor, ini12, fim, limite=LIMITE_BUSCA), ini12
    else:
        linhas, de = db.buscar_vendas(tipo, valor, ini3, fim, limite=LIMITE_BUSCA), ini3
        if not linhas and not verificadas:  # nada recente: olha os 9 meses anteriores (sem reler os 3 já vistos)
            linhas, de = db.buscar_vendas(tipo, valor, ini12, ini3 - dt.timedelta(days=1), limite=LIMITE_BUSCA), ini12
    if verificadas:
        vistos = {r["id_servico"] for r in linhas}
        linhas = sorted(linhas + [r for r in verificadas if r["id_servico"] not in vistos], key=db.ordem_busca,
                        reverse=True)
    return {"itens": [_item_busca(r) for r in linhas[:LIMITE_BUSCA]], "tipo": tipo, "de": de.isoformat(),
            "ate": fim.isoformat(), "truncado": len(linhas) > LIMITE_BUSCA,
            "pode_ampliar": de > ini12 and bool(verificadas or (linhas and tipo == "doc")),
            "fonte": "verificadas+periodo" if verificadas else "periodo", "ampliar_de": ini12.isoformat()}


def alertas_cruzamento(itens, sugestoes):
    """Sugestões dos cruzamentos (SVA: confiança provável; Fixa: vínculo fraco, não comissionada ou não localizada)
    viram alertas na esteira, com o botão para aplicar a correção sugerida."""
    por_venda = defaultdict(list)
    for s in sugestoes:
        por_venda[str(s["id_venda"])].append(s)
    for it in itens:
        if (it.get("verificacao") or {}).get("status") == "CONTESTADA":
            continue  # a venda voltou por contestação: não sugere zerar de novo
        for s in por_venda.get(str(it["id_venda"]), []):
            if s["id_servico"] and s["id_servico"] != it["id_servico"]:
                continue
            if s["origem"] == cruzamento_fixa.ORIGEM:
                alerta = {"nivel": "medio", "txt": f"Cruzamento Fixa ({s['confianca']}): {s['motivo']}"}
                if s.get("correcao"):
                    alerta.update(sugere=s["correcao"], penalidade=s.get("penalidade") or "")
            else:
                alerta = {"nivel": "medio", "txt": f"Cruzamento SVA {s['anomes']} ({s['confianca']}): {s['servico_base']} "
                                                   f"sem ativação vinculada — {s['motivo']}"}
                if s["id_servico"]:  # serviço identificado: pode aplicar a mesma marcação da automação
                    alerta.update(sugere=s.get("correcao") or cruzamento_sva.CORRECAO,
                                  penalidade=s.get("penalidade") or cruzamento_sva.PENALIDADE)
            it["alertas"].insert(0, alerta)


# ---------------------------------------------------------------- registro da verificação
def registro(payload, origem):
    status = (payload.get("status") or "").upper()
    if status not in ("CONFORME", "DIVERGENTE"):
        raise ValueError("Escolha Conforme ou Divergente.")
    receita = _num(origem["receita"]) or 0.0
    reg = {"id_servico": origem["id_servico"], "id_venda": origem["id_venda"], "data_venda": origem["data_venda"],
           "pdv": origem["pdv"], "status": status, "receita_original": receita,
           "observacao": _txt(payload.get("observacao"), 500)}
    for k in ("correcao_vivogo", "tipo_penalidade", "plano_correto", "linha_correta", "documento_correto"):
        reg[k] = None
    reg["receita_correta"] = None
    if status == "DIVERGENTE":
        cats = categorias.CATEGORIAS
        corr = _txt(payload.get("correcao"))
        if corr not in cats["correcoes"]:
            raise ValueError("Selecione o tipo de correção VivoGo.")
        regra = cats["regras"].get(corr, {})
        campos = regra.get("campos", [])
        reg["correcao_vivogo"] = corr
        if "tipo_penalidade" in campos:
            pen = _txt(payload.get("penalidade"))
            if pen not in cats["penalidades"]:
                raise ValueError("Selecione o tipo de penalidade.")
            reg["tipo_penalidade"] = pen
        if "receita_correta" in campos or "receita_correta?" in campos:
            reg["receita_correta"] = _num(payload.get("receita_correta"))
            if "receita_correta" in campos and reg["receita_correta"] is None:
                raise ValueError("Informe a receita correta.")
        if "plano_correto" in campos:
            reg["plano_correto"] = _txt(payload.get("plano_correto"), 255)
            if not reg["plano_correto"]:
                raise ValueError("Informe o plano correto.")
        # Grava só os dígitos, como sempre (Power BI e sistema antigo); o formato do VivoGo é só na tela/exportação
        if "linha_correta" in campos:
            reg["linha_correta"] = formato.normalizar_linha(payload.get("linha_correta"))[:30] or None
            if not reg["linha_correta"]:
                raise ValueError("Informe a linha correta com DDD.")
            erro = formato.erro_linha(reg["linha_correta"])
            if erro:
                raise ValueError("Linha correta inválida: " + erro)
        if "documento_correto" in campos:
            reg["documento_correto"] = digitos(payload.get("documento_correto"))[:30] or None
            if not reg["documento_correto"]:
                raise ValueError("Informe o documento correto.")
            if not doc_valido(reg["documento_correto"]):
                raise ValueError("Documento correto inválido: confira os dígitos do CPF (11) ou CNPJ (14).")
    acao, validada, alterar = categorias.resolver(status, reg["correcao_vivogo"], receita, reg["receita_correta"])
    reg.update(acao_necessaria=acao or None, receita_validada=validada, alterar_vivogo=int(alterar))
    return reg


# ---------------------------------------------------------------- cruzamento SVA
def marcacoes_sva(resultados, anomes):
    regra = categorias.CATEGORIAS["regras"].get(cruzamento_sva.CORRECAO, {})
    marcacoes, sugestoes = [], []
    for r in resultados:
        if r["acao"] == "APLICAR":
            marcacoes.append({
                "id_servico": r["alvo_id_servico"], "id_venda": r["VG_ID_da_venda"], "data_venda": r["VG_Data_venda"],
                "pdv": r["VG_PDV"], "receita": r["VG_Receita"], "correcao": cruzamento_sva.CORRECAO,
                "penalidade": cruzamento_sva.PENALIDADE, "acao": regra.get("acao") or "EXCLUIR VENDA",
                "confianca": r["confianca"], "verificado_por": "Automação SVA",
                "observacao": f"Cruzamento SVA {anomes}: {r['DS_SRVC']} sem ativação vinculada (Flag 0) · "
                              f"{r['confianca']} ({r['criterios']}) · {r['alvo_motivo']}",
            })
        elif r["acao"] == "SUGESTAO":
            sugestoes.append({"id_venda": r["VG_ID_da_venda"], "id_servico": r["alvo_id_servico"],
                              "data_venda": r["VG_Data_venda"], "servico_base": r["DS_SRVC"], "confianca": r["confianca"],
                              "motivo": r["alvo_motivo"] if not r["alvo_id_servico"]
                              else f"confiança {r['confianca']} ({r['criterios']})"})
    return marcacoes, sugestoes


def resumir_linha(r):
    """Campos guardados de cada linha (planilha de resultado e WhatsApp)."""
    campos = ["acao", "situacao", "confianca", "criterios", "alvo_id_servico", "alvo_motivo", "AnoMes", "DT_MVMT",
              "FLAG_VNCL", "NOME_LOJA", "NOME", "DOCUMENTO", "NU_TLFN", "DS_CLSF_SVA", "DS_SRVC", "NO_SRVC",
              "VL_TRCO_FACE", "VG_ID_da_venda", "VG_Data_venda", "VG_PDV", "VG_Vendedor", "VG_Servico", "VG_Plano_novo",
              "VG_N_acesso", "VG_Nome_cliente", "VG_Receita", "VG_Status_servico", "VG_Todos_IDs_venda"]
    return {c: (_iso(r.get(c)) if isinstance(r.get(c), (dt.date, dt.datetime)) else r.get(c)) for c in campos}


def imp_json(i):
    return {**i, "em": _iso(i["em"]), "base_de": _iso(i["base_de"]), "base_ate": _iso(i["base_ate"])}


def sinalizar(linhas):
    """Linhas Flag 0 que existem na VivoGo — as que vão para o WhatsApp das lojas (igual à automação)."""
    return [l for l in linhas if l["acao"] in ("APLICAR", "SUGESTAO")]  # noqa: E741


# ---------------------------------------------------------------- cruzamento Fixa
def marcacoes_fixa(resultados):
    """Fibra cancelada (assertividade Alta) zera só o serviço que a ORDEM indica; o resto vira sugestão."""
    regra = categorias.CATEGORIAS["regras"].get(cruzamento_fixa.CORRECAO, {})
    marcacoes, sugestoes = [], []
    for r in resultados:
        if r["acao"] not in ("APLICAR", "SUGESTAO"):
            continue
        conf = f"{r.get('assertividade') or '-'}" if r.get("assertividade") not in (None, "-") else r["status_cruzamento"]
        anomes = str(r["VG_Data_venda"])[:7].replace("-", "")
        alvo = set(r.get("alvo_ids") or [])
        for sid, srv, rec in r["servicos"]:
            if alvo and sid not in alvo:
                continue  # só o serviço que a ORDEM da Vivo indica (FTTH → banda larga)
            if r["acao"] == "APLICAR":
                marcacoes.append({
                    "id_servico": sid, "id_venda": r["VG_ID_da_venda"], "data_venda": r["VG_Data_venda"], "pdv": r["VG_PDV"],
                    "receita": rec, "correcao": r["correcao"], "penalidade": r.get("penalidade"),
                    "acao": regra.get("acao") or "EXCLUIR VENDA", "confianca": f"Fixa {conf}", "anomes": anomes,
                    "verificado_por": "Automação Fixa",
                    "observacao": f"Cruzamento Fixa: {r['observacao']} · vínculo {conf} ({r.get('metodo') or '-'}) · "
                                  f"CPF confere: {r.get('cpf_confere', '-')}",
                })
            else:
                sugestoes.append({"id_venda": r["VG_ID_da_venda"], "id_servico": sid, "data_venda": r["VG_Data_venda"],
                                  "servico_base": f"ORDEM {r.get('ordem') or '-'} · {srv}", "confianca": conf,
                                  "motivo": r["observacao"], "correcao": r.get("correcao"),
                                  "penalidade": r.get("penalidade"), "anomes": anomes})
    return marcacoes, sugestoes


def resumir_fixa(r):
    campos = ["acao", "status_cruzamento", "assertividade", "metodo", "motivo_vinculo", "observacao", "correcao",
              "penalidade", "VG_ID_da_venda", "VG_Data_venda", "VG_PDV", "adabas", "VG_Vendedor", "VG_Nome_cliente",
              "VG_Servicos", "VG_Servico_alvo", "servico_vivo", "alvo_ids", "VG_Plano_novo", "VG_Receita", "VG_Protocolo",
              "protocolo_tratado", "formato_protocolo",
              "VG_Status_servico", "ordem", "data_vivo", "etapa", "status_inst", "motivo_vivo", "produto",
              "vendedor_vivo", "comissionada", "cpf_confere", "endereco", "endereco_detalhe", "VG_Endereco"]
    out = {c: (_iso(r.get(c)) if isinstance(r.get(c), (dt.date, dt.datetime)) else r.get(c)) for c in campos}
    out["servicos"] = [[x["id_servico"], x["servico"] or x["servico_grupo"], float(x["receita"] or 0)]
                       for x in r.get("_servicos", [])]
    return out


# ---------------------------------------------------------------- planilhas .xlsx
SEGMENTO = {"Móvel": "MOVEL", "SVA": "SVA", "Fixa": "FIXA", "Seguros": "SEGURO"}
CABECALHO_PLANILHA = [
    "FAROL", "Filial", "Serviço", "Nº da Venda", "Data", "Data Última Ativação", "Vendedor", "Nome Cliente",
    "CPF/CNPJ", "DOCUMENTO", "Endereço", "Cidade", "UF", "Plano", "Acesso", "Status do Serviço", "Valor Plano",
    "Receita", "Segmentação", "Status", "CORREÇÃO VIVO GO", "TIPO PENALIDADE", "AÇÃO NECESSÁRIA",
    "RECEITA CORRETA", "PLANO CORRETO", "LINHA CORRETA", "DOCUMENTO CORRETO", "RECEITA VALIDADA",
    "ALTERAR VIVO GO", "VENDA DUPLICADA", "Alerta Número Fake", "OBSERVAÇÃO", "VERIFICADO POR", "VERIFICADO EM",
    "CONTESTAÇÃO", "CONTESTADO POR", "MOTIVO CONTESTAÇÃO", "CONTESTAÇÃO ANALISADA POR", "CONTESTAÇÃO EM"]


def planilha_auditoria(inicio, fim):
    """Todas as vendas do mês no layout da aba *base* da planilha (bytes do .xlsx)."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    duplicados = indice_mes(inicio, fim)["duplicados"]
    linhas = db.vendas_periodo(inicio, fim)
    cab = CABECALHO_PLANILHA
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "base"
    ws.append(cab)
    for r in linhas:
        st = r["v_status"]
        farol = 2 if st == "CONFORME" else 1 if st == "DIVERGENTE" else 0
        receita = _num(r["receita"])
        ws.append([
            farol, r["pdv"], r["servico"], _int(r["id_venda"]), r["data_venda"], r["ultima_ativacao"], r["vendedor"],
            r["cliente"], formato.doc_vivogo(r["cpf"]) or None, formato.doc_vivogo(r["documento"]) or None, r["endereco"],
            r["cidade_cliente"], r["uf_cliente"], r["plano"], formato.linha_vivogo(r["acesso"]) or None, r["status_servico"],
            _num(r["valor_plano"]), receita,
            SEGMENTO.get(r["pilar"], (r["pilar"] or "").upper()),
            {"CONFORME": "Em Conformidade", "DIVERGENTE": "Divergente", "CONTESTADA": "Contestada (venda volta)"}.get(st, "Pendente"),
            r["v_correcao"], r["v_penalidade"], r["v_acao"] or "", _num(r["v_receita_correta"]), r["v_plano_correto"],
            formato.linha_vivogo(r["v_linha_correta"]) or None, formato.doc_vivogo(r["v_documento_correto"]) or None,
            _num(r["v_receita_validada"]) if st else receita,
            "Sim" if r["v_alterar_vivogo"] else ("Não" if st else ""),
            "DUPLICADO" if duplicados.get(r["id_servico"], {}).get("original", r["id_servico"]) != r["id_servico"]
            else "UNICO",
            "SIM" if r["acesso"] and numero_suspeito(r["acesso"]) else "NÃO",
            r["v_observacao"], r["v_por"], r["v_em"],
            r["v_contestacao_decisao"], r["v_contestacao_solicitante"], r["v_contestacao_motivo"], r["v_contestacao_por"],
            r["v_contestacao_em"],
        ])
    roxo = PatternFill("solid", fgColor="571381")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = roxo
        c.alignment = Alignment(vertical="center")
    for i, col in enumerate(cab, 1):
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(32, len(col) + 4))
    # Acesso, CPF/CNPJ, linha e documento corretos vão como texto no formato do VivoGo ("27 99999-9999",
    # "999.999.999-99"): como número o Excel comia o zero da frente do CPF. Valores continuam número (somam no
    # Excel), com duas casas — no Excel em português aparecem como no VivoGo ("100,75").
    moeda = [cab.index(c) for c in ("Valor Plano", "Receita", "RECEITA CORRETA", "RECEITA VALIDADA")]
    for row in ws.iter_rows(min_row=2):
        for idx in (4, 5, 33, 38):
            if row[idx].value:
                row[idx].number_format = "dd/mm/yyyy" if idx not in (33, 38) else "dd/mm/yyyy hh:mm"
        for idx in moeda:
            if row[idx].value is not None:
                row[idx].number_format = "#,##0.00"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ABAS_SVA = [("Zeradas automaticamente", "APLICAR"), ("Sugestões (revisar)", "SUGESTAO"), ("OK - serão pagas", "OK"),
            ("SVA VivoGo fora da base", "SEM_BASE"), ("Base sem SVA na VivoGo", "NAO_EXISTE"),
            ("SVA embutido no plano", "PLANO"), ("Tudo", None)]
ABAS_FIXA = [("Zeradas automaticamente", "APLICAR"), ("Sugestões (revisar)", "SUGESTAO"), ("OK - instaladas", "OK"),
             ("Instalação pendente", "PENDENTE"), ("Venda B2B", "B2B"), ("Vivo sem venda na VivoGo", "NAO_LANCADA"),
             ("Fora da base (outro serviço)", "FORA"),
             ("Tudo", None)]


def planilha_resultado(imp, linhas, abas):
    """Resultado gravado de uma importação, uma aba por ação (bytes do .xlsx)."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    cols = list(dict.fromkeys(c for l in linhas for c in l))  # noqa: E741
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    roxo = PatternFill("solid", fgColor="571381")
    for nome, acao in abas:
        ws = wb.create_sheet(nome)
        ws.append(cols)
        for l in linhas:  # noqa: E741
            if acao is None or l["acao"] == acao:
                ws.append([", ".join(map(str, l[c])) if isinstance(l.get(c), list) else l.get(c) for c in cols])
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = roxo
        ws.freeze_panes = "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(40, max(10, max(len(str(c.value or "")) for c in col[:200]) + 2))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- prévias dos cruzamentos (entre a prévia e o aplicar)
# No sistema antigo ficavam num dict do processo; no portal há vários workers, então vão para o cache
# compartilhado (comprimidas), com o dict do processo como reserva se o Redis estiver fora.
_previas_locais = {}


def guardar_previa(tipo, token, dados):
    blob = zlib.compress(json.dumps(dados, default=str, ensure_ascii=False).encode("utf-8"))
    cache.set(f"auditoria_d1:previa:{tipo}:{token}", blob, 3600)
    _previas_locais[(tipo, token)] = blob
    while len(_previas_locais) > 6:
        _previas_locais.pop(next(iter(_previas_locais)))


def tirar_previa(tipo, token):
    if not token:
        return None
    chave = f"auditoria_d1:previa:{tipo}:{token}"
    blob = cache.get(chave) or _previas_locais.get((tipo, token))
    cache.delete(chave)
    _previas_locais.pop((tipo, token), None)
    if not blob:
        return None
    return json.loads(zlib.decompress(blob).decode("utf-8"))
