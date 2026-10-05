"""Apoio da tela /users/manage/system-config/ (versões do comissionamento).

Dois serviços:

- ``limpar_cache_comissionamento``: o que o botão "Limpar Cache" fazia, e mais.
  O arquivo baixado do OneDrive fica no cache pela chave
  ``<prefixo>_<hash da URL>_file_content`` (``download_excel_file``), e os dados
  já lidos ficam em ``commission_*``, ``metas_pilar*``, ``iq_data_*``… — o save
  da versão apagava nomes fixos que não existem mais. Com o Redis do portal dá
  para apagar por padrão; sem ele, apaga o que tem nome conhecido.
- ``previa_planilha``: baixa o link e devolve as abas, o cabeçalho e as
  primeiras linhas, conferindo as abas e colunas que o comissionamento lê. É a
  pré-visualização da tela: dá para ver se o link é o certo antes de salvar.
"""
import logging
import unicodedata
from io import BytesIO

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Prefixos das chaves de cache do comissionamento e da contestação.
PADROES_CACHE = (
    'comissao_*', 'remuneracao_cn_*', 'base_pagamento*', 'base_exclusao*',
    'vendas_*', 'lojas_coord*', 'commission_data_*', 'commission_all_users_*',
    'metas_pilar*', 'iq_data_*', 'aparte_realized_*', 'contestacao_base_*',
    'previa_planilha_*',
)
CHAVES_FIXAS = (
    'vendas_all_data', 'vendas_file_content', 'vendas_all_file_content', 'metas_por_pilar',
    'iq_data', 'vendedores_por_filial', 'lojas_por_coordenador',
    'contestacao_base_exclusao_content', 'contestacao_base_pagamento_content',
)

# O que cada link precisa ter. ``abas``: obrigatórias; ``opcionais``: lidas se
# existirem; ``colunas``: procuradas na primeira aba obrigatória (sem acento,
# maiúsculas, por "contém").
TIPOS = {
    'excel_comissao_url': {
        'rotulo': 'Planilha de Comissionamento',
        'abas': ['REMUNERAÇÃO CN', 'REMUNERAÇÃO GERENTE', 'REMUNERAÇÃO COO e SNP', 'ÍNDICE DE QUALIDADE'],
        'opcionais': ['REMUNERAÇÃO RECEPCIONISTA', 'RESULTADO COO SNP'],
        'colunas': [],
    },
    'excel_vendas_url': {
        'rotulo': 'Planilha de Vendas e Metas',
        'abas': [], 'opcionais': ['VENDAS'], 'colunas': [],
    },
    'excel_base_pagamento_url': {
        'rotulo': 'BASE_PAGAMENTO',
        'abas': ['Planilha1'], 'opcionais': [], 'colunas': ['FILIAL', 'VENDEDOR', 'RECEITA', 'PILAR'],
    },
    'excel_base_exclusao_url': {
        'rotulo': 'BASE_EXCLUSAO (Comissionamento)',
        'abas': ['Planilha1'], 'opcionais': [], 'colunas': [],
    },
    'excel_contestacao_base_exclusao_url': {
        'rotulo': 'BASE_EXCLUSAO (Contestação)',
        'abas': ['Planilha1'], 'opcionais': [], 'colunas': [],
    },
    'excel_contestacao_base_pagamento_url': {
        'rotulo': 'BASE_PAGAMENTO (Contestação)',
        'abas': ['Planilha1'], 'opcionais': [], 'colunas': ['RECEITA', 'RECEBIDO'],
    },
}

LINHAS_AMOSTRA = 5
COLUNAS_AMOSTRA = 12


def limpar_cache_comissionamento():
    """Apaga o cache das planilhas e dos dados do comissionamento. Devolve quantas chaves saíram."""
    apagadas = 0
    apagar_por_padrao = getattr(cache, 'delete_pattern', None)
    if apagar_por_padrao:
        for padrao in PADROES_CACHE:
            try:
                apagadas += apagar_por_padrao(padrao) or 0
            except Exception as exc:                                # noqa: BLE001 — cache nunca derruba o save
                logger.warning('Cache do comissionamento: falha ao apagar %s: %s', padrao, exc)
    try:
        cache.delete_many(list(CHAVES_FIXAS))
    except Exception as exc:                                        # noqa: BLE001
        logger.warning('Cache do comissionamento: falha ao apagar chaves fixas: %s', exc)
    return apagadas


def _sem_acento(texto):
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    return ''.join(c for c in texto if not unicodedata.combining(c)).upper().strip()


def _celula(valor):
    if valor is None:
        return ''
    if hasattr(valor, 'strftime'):
        return valor.strftime('%d/%m/%Y')
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()[:60]


def _ler_aba(ws):
    """Cabeçalho (primeira linha com conteúdo), as primeiras linhas e o total de linhas."""
    cabecalho, amostra, total = None, [], 0
    for linha in ws.iter_rows(values_only=True):
        valores = [_celula(v) for v in linha[:60]]
        if not any(valores):
            continue
        if cabecalho is None:
            cabecalho = valores
            continue
        total += 1
        if len(amostra) < LINHAS_AMOSTRA:
            amostra.append(valores[:COLUNAS_AMOSTRA])
        if total >= 20000:          # planilha enorme: o número exato não importa aqui
            break
    cabecalho = cabecalho or []
    while cabecalho and not cabecalho[-1]:
        cabecalho.pop()
    return cabecalho, amostra, total


def previa_planilha(url, tipo):
    """Baixa o link e descreve a planilha. Nunca levanta: erro vai em ``erro``."""
    from openpyxl import load_workbook

    from users.commission_views import download_excel_file

    regra = TIPOS.get(tipo, {'rotulo': 'Planilha', 'abas': [], 'opcionais': [], 'colunas': []})
    resposta = {'ok': False, 'tipo': tipo, 'rotulo': regra['rotulo'], 'abas': [],
                'faltando': [], 'colunas_faltando': [], 'avisos': [], 'erro': ''}
    url = (url or '').strip()
    if not url.lower().startswith(('http://', 'https://')):
        resposta['erro'] = 'Isso não parece um link (precisa começar com https://).'
        return resposta

    arquivo, erro = download_excel_file(url, 'previa_planilha')
    if erro or arquivo is None:
        resposta['erro'] = (f'Não deu para baixar a planilha ({erro}). Confira se o link está como '
                            '"Qualquer pessoa com o link".')
        return resposta
    try:
        livro = load_workbook(BytesIO(arquivo.getvalue()), read_only=True, data_only=True)
    except Exception as exc:                                        # noqa: BLE001
        resposta['erro'] = f'O link baixa, mas não é uma planilha Excel que dê para abrir ({exc}).'
        return resposta

    try:
        nomes = list(livro.sheetnames)
        por_nome = {_sem_acento(n): n for n in nomes}
        resposta['faltando'] = [a for a in regra['abas'] if _sem_acento(a) not in por_nome]
        principal = next((por_nome[_sem_acento(a)] for a in regra['abas'] + regra['opcionais']
                          if _sem_acento(a) in por_nome), nomes[0] if nomes else None)
        for nome in nomes[:30]:
            ws = livro[nome]
            esperada = _sem_acento(nome) in {_sem_acento(a) for a in regra['abas'] + regra['opcionais']}
            item = {'nome': nome, 'esperada': esperada, 'principal': nome == principal}
            if nome == principal:
                cabecalho, amostra, total = _ler_aba(ws)
                item.update({'cabecalho': cabecalho[:COLUNAS_AMOSTRA], 'colunas': len(cabecalho),
                             'linhas': total, 'amostra': amostra})
                if regra['colunas']:
                    presentes = [_sem_acento(c) for c in cabecalho]
                    resposta['colunas_faltando'] = [
                        c for c in regra['colunas'] if not any(c in p for p in presentes)]
                if total == 0:
                    resposta['avisos'].append(f'A aba "{nome}" está sem linhas de dados.')
            resposta['abas'].append(item)
        if len(nomes) > 30:
            resposta['avisos'].append(f'Mostrando 30 de {len(nomes)} abas.')
    finally:
        livro.close()

    resposta['ok'] = not resposta['faltando'] and not resposta['colunas_faltando']
    return resposta
