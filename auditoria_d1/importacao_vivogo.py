# -*- coding: utf-8 -*-
"""Arquivo de importação do VivoGo (layout de modelo_importacao_ged.csv) montado direto do banco.

Cada venda alterada na auditoria vira uma linha do modelo, preenchida com os dados da vendas_servicos e com as
correções aplicadas nos campos que o VivoGo permite alterar (marcados com (**) no modelo):
  VENDA INEXISTENTE / MOVIMENTO INELEGÍVEL → Status BKO = Improcedente + Motivo Reprovação
  VALORES INCORRETOS (e PLANO INCORRETO com receita correta) → Valor Plano ajustado para a receita correta
  LINHA INCORRETA, DIVERGÊNCIA CADASTRAL, nome do PLANO → não são campos alteráveis: ficam de fora e são
  avisados como ajuste manual. VENDA EM DUPLICIDADE não altera nada no VivoGo.
  CONTESTADA (contestação procedente) → a linha DESFAZ a alteração: exclusão volta com Status BKO = Procedente e
  sem motivo de reprovação; valor corrigido volta ao valor original da venda.

A coluna "Serviço(*)" sai com o nome do serviço como vem na venda (Serviço_Técnico: "Alta Controle",
"Alta Banda Larga", "Upgrade de Controle-Controle"...), a pedido do usuário (2026-11-11). servico_vivogo() — o nome no
padrão do relatório Gestão Documental ("Alta Smartphone Controle") — só é usado se o Serviço_Técnico vier vazio.
"""
import csv
import io
import os
import re

MODELO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modelo_importacao_ged.csv")

COLUNAS_PADRAO = [
    "Filial", "Data Venda(*)", "Data Ativação(**)", "Status BKO(*)(**)", "Serviço(*)", "ID Venda", "Modelo", "IMEI",
    "Plano", "Valor Plano(**)", "Plano Antigo", "Valor Plano Antigo(**)", "Vencimento Fatura(**)", "Vendedor", "Cliente",
    "CPF/CNPJ", "Nº Acesso(*)", "Nº Portado", "Servico Instalado", "Data Agendada de Instalação", "Armário",
    "Data de Instalação", "Situação do serviço(**)", "Receita", "Status GED", "Sim Card", "COD", "Fidelização",
    "Zerar Remuneração", "Gerar Price", "Nº Solicitação NEXT / RPON", "Nº Protocolo GED(**)",
    "Nº Protocolo GED Portabilidade", "Motivo Reprovação(**)", "Data Digitalização", "Usuário Inserção",
    "Usuário Alteração", "Observação", "Motivo Cancelamento(**)", "Comissão de Serviço do Vendedor",
    "Comissão de Aparelho do Vendedor(**)", "Data Última Ativação(**)", "Data de alteração", "Usuário Cancelamento",
    "Data Cancelamento", "Líder de Equipe", "Status Primeira Fatura(**)", "Data Primeira Fatura(**)",
    "Status Segunda Fatura(**)", "Data Segunda Fatura(**)", "Status Terceira Fatura(**)", "Data Terceira Fatura(**)",
]

# Motivo de reprovação quando a venda é excluída (valores existentes no VivoGo)
MOTIVO_REPROVACAO = {
    "VENDA INEXISTENTE": "Lançamento incorreto no Vivo GO",
    "MOVIMENTO INELEGÍVEL": "Outros erros",
}
MOTIVO_MANUAL = {
    "LINHA INCORRETA": "Nº de acesso não é alterável pela importação",
    "DIVERGÊNCIA CADASTRAL": "CPF/CNPJ não é alterável pela importação",
    "PLANO INCORRETO": "Nome do plano não é alterável pela importação",
}

# Tecnologia do serviço no VivoGo a partir do Serviço_Técnico
TECNOLOGIA = {
    "alta banda larga": "Internet Fibra", "upgrade fixa": "Internet Fibra", "downgrade de plano fixo": "Internet Fibra",
    "migração tecnológica": "Internet Fibra", "alta tv": "TV GVT", "alta fixo": "Fixa GVT", "sva fixo": "TV GVT",
    "alta internet móvel": "Modem", "portabilidade fixa": "Fixa GVT",
}
# SVAs móveis são "Outros serviços", exceto estes (pelo início do nome do plano)
SVA_SMARTPHONE = ("vale saude - ", "automonitoramento 1 camera")
SVA_VOZ = ("pacote mensal",)

digitos = lambda s: re.sub(r"\D", "", str(s or ""))


def servico_vivogo(v):
    mov = (v.get("servico_grupo") or "").strip()
    tec_ = (v.get("servico") or "").strip().lower()
    plano = (v.get("plano") or "").strip().lower()
    grupo = (v.get("grupo_plano") or "").strip() or "Pós"
    fixa = tec_ in TECNOLOGIA and TECNOLOGIA[tec_] in ("Internet Fibra", "TV GVT", "Fixa GVT")
    if mov == "Seguro":
        return f"Seguro {grupo}"
    if mov == "Reativação":
        return f"Reativação {'Fixa' if fixa else grupo}"
    if mov == "SVA" and tec_ == "sva móvel":
        tec = ("Smartphone" if plano.startswith(SVA_SMARTPHONE) else "Voz" if plano.startswith(SVA_VOZ)
               else "Outros serviços")
    elif "casa 5g" in plano:
        tec = "Modem"
    elif tec_ in TECNOLOGIA:
        tec = "Internet Fixa" if plano.startswith("internet fixa") else TECNOLOGIA[tec_]
    elif grupo == "Pré":
        tec = "Voz"
    else:
        tec = "Smartphone"
    return f"{mov} {tec} {grupo}"


def colunas_modelo():
    if os.path.exists(MODELO):
        try:
            cols = open(MODELO, "rb").readline().decode("cp1252").strip().split(";")
            if len(cols) > 10:
                return cols
        except (OSError, UnicodeDecodeError):
            pass
    return COLUNAS_PADRAO


def _data(v):
    return v.strftime("%d/%m/%Y") if v else ""


def _num(v):
    return "" if v is None or v == "" else f"{float(v):.2f}".replace(".", ",")


def _aspas(v):
    """Campos numéricos longos vão entre apóstrofos no VivoGo ('8955...') para o Excel não estragar."""
    return f"'{str(v).strip()}'" if v not in (None, "") else "''"


def _sim_nao(v):
    s = str(v or "").strip().lower()
    return "Sim" if s in ("1", "sim", "s", "true") else "Não" if s in ("0", "não", "nao", "n", "false") else (v or "")


def _novo_valor_plano(v, receita_correta):
    """O VivoGo calcula a receita a partir do valor do plano (trocas/migrações: novo − antigo)."""
    vp, va, rec = v["valor_plano"], v["valor_plano_antigo"], v["receita"]
    if va and vp is not None and rec is not None and abs(float(vp) - float(va) - float(rec)) < 0.01:
        return float(va) + float(receita_correta)
    return float(receita_correta)


def _situacao(v):
    """Situação do serviço no VivoGo (análise do GED): cancelado → Cancelado; com protocolo GED ou já avaliado
    pelo BKO → Confirmado; senão Não avaliado."""
    if (v["status_servico"] or "").lower() == "cancelado":
        return "Cancelado"
    if digitos(v["protocolo_ged"]) or (v["status_bko"] or "Não avaliado") != "Não avaliado":
        return "Confirmado"
    return "Não avaliado"


def linha_base(v):
    """Linha do modelo com os dados atuais da venda (chaves = nomes do modelo sem marcadores)."""
    cancelado = (v["status_servico"] or "").lower() == "cancelado"
    instalado = (v["status_instalacao"] or "").lower() == "instalado"
    situacao = _situacao(v)
    return {
        "Filial": v["pdv"], "Data Venda": _data(v["data_venda"]), "Data Ativação": _data(v["data_ativacao"]),
        "Status BKO": v["status_bko"] or "Não avaliado", "Serviço": (v["servico"] or "").strip() or servico_vivogo(v),
        "ID Venda": v["id_venda"],
        "Modelo": "", "IMEI": "", "Plano": v["plano"] or "", "Valor Plano": _num(v["valor_plano"]),
        "Plano Antigo": v["plano_antigo"] or "-", "Valor Plano Antigo": _num(v["valor_plano_antigo"]),
        "Vencimento Fatura": v["vencimento"] or "", "Vendedor": v["vendedor"] or "", "Cliente": v["cliente"] or "",
        "CPF/CNPJ": v["cpf"] or v["documento"] or "", "Nº Acesso": v["acesso"] or "", "Nº Portado": v["portabilidade"] or "",
        "Servico Instalado": "Sim" if instalado else "Não", "Data Agendada de Instalação": "",
        "Armário": "", "Data de Instalação": _data(v["data_instalacao"]) if instalado else "",
        "Situação do serviço": situacao, "Receita": _num(v["receita"]),
        "Status GED": ("Serviço Digitalizado" if digitos(v["protocolo_ged"]) else "Serviço cancelado" if cancelado
                       else "Serviço Não digitalizado"),
        "Sim Card": _aspas(v["iccid"]), "COD": _sim_nao(v["conta_digital"]), "Fidelização": _sim_nao(v["fidelizado"]),
        "Zerar Remuneração": _sim_nao(v["zerar_remuneracao"]), "Gerar Price": "Não",
        "Nº Solicitação NEXT / RPON": _aspas(v["protocolo"]), "Nº Protocolo GED": _aspas(v["protocolo_ged"]),
        "Nº Protocolo GED Portabilidade": "''", "Motivo Reprovação": "", "Data Digitalização": "",
        "Usuário Inserção": "", "Usuário Alteração": "", "Observação": "",
        "Motivo Cancelamento": v["motivo_cancelamento"] or ("Análise de Pós venda" if cancelado else ""),
        "Comissão de Serviço do Vendedor": "Sim", "Comissão de Aparelho do Vendedor": "Não", "Data Última Ativação": _data(v["ultima_ativacao"]),
        "Data de alteração": "", "Usuário Cancelamento": v["usuario_cancelamento"] or "",
        "Data Cancelamento": _data(v["data_cancelamento"]), "Líder de Equipe": "",
    }


def _sem_marcador(col):
    return re.sub(r"\(\*+\)", "", col).strip()


def desfazer(v, corr, ident, manuais):
    """Campos que voltam a venda contestada ao que era antes da auditoria."""
    if corr in MOTIVO_REPROVACAO:
        return {"Status BKO": "Procedente", "Motivo Reprovação": ""}
    if corr in ("VALORES INCORRETOS", "PLANO INCORRETO") and v["v_receita_correta"] is not None:
        original = v.get("v_receita_original")
        if original is None:
            original = v["receita"]
        return {"Valor Plano": _num(_novo_valor_plano(v, original))}
    if corr in MOTIVO_MANUAL:
        manuais.append({**ident, "motivo": "Contestação procedente: desfazer o ajuste manual feito no VivoGo",
                        "valor": "", "contestada": True})
    return {}


def gerar(vendas):
    """vendas: linhas de db.vendas_divergentes. Devolve (bytes do CSV, resumo)."""
    cols = colunas_modelo()
    saida, incluidas, manuais = [], [], []
    for v in vendas:
        corr = v["v_correcao"]
        ident = {"id_venda": v["id_venda"], "servico": v["servico"], "correcao": corr, "data": _data(v["data_venda"])}
        if v.get("v_status") == "CONTESTADA":
            alt = desfazer(v, corr, ident, manuais)
            if alt:
                linha = {**linha_base(v), **alt}
                saida.append([linha.get(_sem_marcador(c), "") for c in cols])
                incluidas.append({**ident, "contestada": True})
            continue
        alt = {}
        if corr in MOTIVO_REPROVACAO:
            alt = {"Status BKO": "Improcedente", "Motivo Reprovação": MOTIVO_REPROVACAO[corr]}
        elif v["v_receita_correta"] is not None and corr in ("VALORES INCORRETOS", "PLANO INCORRETO"):
            alt = {"Valor Plano": _num(_novo_valor_plano(v, v["v_receita_correta"]))}
        if corr in MOTIVO_MANUAL:
            manuais.append({**ident, "motivo": MOTIVO_MANUAL[corr],
                            "valor": v["v_linha_correta"] or v["v_documento_correto"] or v["v_plano_correto"] or ""})
        if not alt:
            continue
        linha = {**linha_base(v), **alt}
        saida.append([linha.get(_sem_marcador(c), "") for c in cols])
        incluidas.append(ident)
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(cols)
    w.writerows(saida)
    return buf.getvalue().encode("cp1252", errors="replace"), {"incluidas": incluidas, "manuais": manuais}
