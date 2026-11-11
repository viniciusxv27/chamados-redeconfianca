# -*- coding: utf-8 -*-
"""Categorias de problema da verificação (cópia embutida das abas da Pasta1.xlsx do sistema D-1).

- Aba "tipo correção vivogo": o tipo de correção (categoria principal da divergência).
- Aba "tipo penalidade": o motivo da penalidade. Na planilha base toda penalidade vem com a correção
  MOVIMENTO INELEGÍVEL (venda excluída, receita validada 0), então a penalidade é o detalhe dessa correção.

No portal a lista embutida é a fonte (a planilha não é lida em produção). A ORDEM importa: os atalhos 1–7 da
esteira seguem esta ordem, igual ao sistema antigo.

As regras de ação/receita reproduzem o que a planilha base já pratica:
  correção              → AÇÃO NECESSÁRIA     campo a informar        RECEITA VALIDADA
  VENDA INEXISTENTE     → EXCLUIR VENDA       -                        0
  MOVIMENTO INELEGÍVEL  → EXCLUIR VENDA       tipo de penalidade       0
  VALORES INCORRETOS    → CORRIGIR RECEITA    receita correta          receita correta
  PLANO INCORRETO       → CORRIGIR PLANO      plano correto (+receita) receita correta, se informada
  LINHA INCORRETA       → ALTERAR Nº ACESSO   linha correta            receita da venda
  DIVERGÊNCIA CADASTRAL → (sem ação)          documento correto        receita da venda
  VENDA EM DUPLICIDADE  → (sem ação)          -                        receita da venda
"""

CORRECOES_PADRAO = ["VENDA INEXISTENTE", "VALORES INCORRETOS", "PLANO INCORRETO", "LINHA INCORRETA",
                    "DIVERGÊNCIA CADASTRAL", "MOVIMENTO INELEGÍVEL", "VENDA EM DUPLICIDADE"]
PENALIDADES_PADRAO = ["MD DE UP", "MD DE BAIXA", "MD DE ALTA", "MESMO NUMERO", "REALTA", "PREMATURA DE UP",
                      "ORDEM REEMITIDA", "MD MIGRACAO", "SUSPENSAO DE ALTA", "INVERSÃO TITULARIDADE",
                      "SUSPENSAO DE MIGRACAO", "SUSPENSAO", "PREMATURA DE MIGRACAO", "SUSPENSAO DE UP", "IP",
                      "SEM ASSINATURA DIGITAL", "REPROVADO", "SVA NÃO REMUNERADO", "FORA DA CARÊNCIA",
                      "FIBRA CANCELADA", "DOWNGRADE COM INCLUSÃO SVA", "SVA NÃO ATIVADO"]

REGRAS = {
    "VENDA INEXISTENTE": {"acao": "EXCLUIR VENDA", "zera": True, "campos": [],
                          "dica": "A venda não existe no sistema da operadora."},
    "MOVIMENTO INELEGÍVEL": {"acao": "EXCLUIR VENDA", "zera": True, "campos": ["tipo_penalidade"],
                             "dica": "Movimento não elegível à remuneração. Informe a penalidade."},
    "VALORES INCORRETOS": {"acao": "CORRIGIR RECEITA", "zera": False, "campos": ["receita_correta"],
                           "dica": "A receita lançada não confere com o valor real."},
    "PLANO INCORRETO": {"acao": "CORRIGIR PLANO", "zera": False, "campos": ["plano_correto", "receita_correta?"],
                        "dica": "O plano lançado não é o plano ativado."},
    "LINHA INCORRETA": {"acao": "ALTERAR Nº ACESSO", "zera": False, "campos": ["linha_correta"],
                        "dica": "O número de acesso lançado está errado."},
    "DIVERGÊNCIA CADASTRAL": {"acao": "", "zera": False, "campos": ["documento_correto"],
                              "dica": "CPF/CNPJ do cliente diferente do titular da linha."},
    "VENDA EM DUPLICIDADE": {"acao": "", "zera": False, "campos": [],
                             "dica": "Mesma venda lançada mais de uma vez."},
}


def carregar():
    regras = {c: REGRAS.get(c, {"acao": "", "zera": False, "campos": [], "dica": ""}) for c in CORRECOES_PADRAO}
    return {"correcoes": list(CORRECOES_PADRAO), "penalidades": list(PENALIDADES_PADRAO), "regras": regras,
            "origem": "padrão embutido"}


CATEGORIAS = carregar()


def resolver(status, correcao, receita_original, receita_correta):
    """Devolve (ação necessária, receita validada, alterar vivogo) conforme as regras da planilha."""
    if status != "DIVERGENTE":
        return "", receita_original, False
    regra = CATEGORIAS["regras"].get(correcao, {})
    if regra.get("zera"):
        return regra.get("acao", ""), 0.0, True
    if receita_correta is not None:
        return regra.get("acao", ""), receita_correta, True
    return regra.get("acao", ""), receita_original, True
