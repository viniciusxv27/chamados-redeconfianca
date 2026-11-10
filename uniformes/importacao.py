"""Importação do estoque por planilha (Excel/CSV) e exportações.

A planilha da rede ainda não chegou, então a leitura é tolerante: acha a linha
do cabeçalho nas 15 primeiras, aceita sinônimos ("Peça", "Qtd", "Tam.",
"Modelo"…), ignora maiúsculas e acentos e soma linhas repetidas do mesmo item.
Nada grava na leitura: a tela mostra a prévia e só o "Confirmar" lança.
"""
from __future__ import annotations

import csv
import io
from collections import OrderedDict

import openpyxl
from django.db import transaction
from django.utils import timezone

from . import servicos
from .models import ItemUniforme
from .servicos import ErroEstoque, chave_item, limpar_descricao, limpar_tamanho, limpar_tipo, norm

SINONIMOS = {
    'tipo': ['tipo', 'peca', 'item', 'uniforme', 'produto', 'tipo de peca', 'tipo do uniforme', 'nome'],
    'tamanho': ['tamanho', 'tam', 'tam.', 'numeracao', 'numero', 'tamanhos'],
    'quantidade': ['quantidade', 'qtd', 'qtd.', 'qtde', 'qtde.', 'quant', 'quant.', 'estoque', 'saldo',
                   'quantidade em estoque', 'qtd estoque', 'total'],
    'descricao': ['descricao', 'modelo', 'cor', 'observacao', 'obs', 'detalhe', 'descricao/modelo'],
    'minimo': ['estoque minimo', 'minimo', 'est. minimo', 'qtd minima'],
}

MODO_ENTRADA = 'ENTRADA'   # soma a planilha ao que já existe
MODO_ACERTAR = 'ACERTAR'   # o saldo passa a ser o da planilha (ajuste de inventário)


def _coluna(cabecalho):
    texto = norm(cabecalho).rstrip(':')
    for campo, nomes in SINONIMOS.items():
        if texto in nomes:
            return campo
    return None


def _linhas_brutas(arquivo, nome):
    nome = (nome or '').lower()
    if nome.endswith('.csv'):
        conteudo = arquivo.read()
        if isinstance(conteudo, bytes):
            try:
                conteudo = conteudo.decode('utf-8-sig')
            except UnicodeDecodeError:
                conteudo = conteudo.decode('latin-1')
        dialeto = csv.Sniffer().sniff(conteudo[:2000], delimiters=';,\t') if conteudo.strip() else csv.excel
        return [list(linha) for linha in csv.reader(io.StringIO(conteudo), dialeto)]
    try:
        livro = openpyxl.load_workbook(arquivo, read_only=True, data_only=True)
    except Exception as exc:                                   # noqa: BLE001
        raise ErroEstoque('Não consegui abrir a planilha. Envie um arquivo .xlsx ou .csv.') from exc
    linhas = []
    for aba in livro.worksheets:          # primeira aba que tiver cabeçalho reconhecível
        linhas = [list(linha) for linha in aba.iter_rows(values_only=True)]
        if _achar_cabecalho(linhas)[0] is not None:
            break
    livro.close()
    return linhas


def _achar_cabecalho(linhas):
    for indice, linha in enumerate(linhas[:15]):
        mapa = {}
        for pos, valor in enumerate(linha):
            campo = _coluna(valor) if valor is not None else None
            if campo and campo not in mapa:
                mapa[campo] = pos
        if 'quantidade' in mapa and ('tipo' in mapa or 'descricao' in mapa):
            return indice, mapa
    return None, {}


def ler_planilha(arquivo, nome=''):
    """Lê e devolve a prévia: {'linhas': [...], 'avisos': [...], 'colunas': {...}}.

    Cada linha: tipo, tamanho, descricao, quantidade, minimo, item_id (se já
    existe), saldo_atual. Linhas iguais são somadas.
    """
    brutas = _linhas_brutas(arquivo, nome)
    indice, mapa = _achar_cabecalho(brutas)
    if indice is None:
        raise ErroEstoque('Não achei o cabeçalho. A planilha precisa de uma coluna de quantidade '
                          'e uma de tipo/peça (ou descrição) — ex.: Tipo | Tamanho | Quantidade | Descrição.')

    def valor(linha, campo):
        pos = mapa.get(campo)
        return linha[pos] if pos is not None and pos < len(linha) else None

    existentes = {chave_item(i.tipo, i.tamanho, i.descricao): i for i in ItemUniforme.objects.all()}
    juntas, avisos = OrderedDict(), []
    for numero, linha in enumerate(brutas[indice + 1:], start=indice + 2):
        if not any(str(c).strip() for c in linha if c is not None):
            continue
        tipo = limpar_tipo(valor(linha, 'tipo'))
        descricao = limpar_descricao(valor(linha, 'descricao'))
        if not tipo and descricao:              # planilha só com descrição: ela vira o tipo
            tipo, descricao = limpar_tipo(descricao), ''
        if not tipo:
            avisos.append(f'Linha {numero}: sem tipo/peça — ignorada.')
            continue
        bruto_qtd = valor(linha, 'quantidade')
        if bruto_qtd is None or str(bruto_qtd).strip() in ('', '-'):
            quantidade = 0
        else:
            try:
                numero_qtd = float(str(bruto_qtd).strip().replace(',', '.'))
            except ValueError:
                avisos.append(f'Linha {numero}: quantidade "{bruto_qtd}" não é número — ignorada.')
                continue
            if numero_qtd < 0 or numero_qtd != int(numero_qtd):
                avisos.append(f'Linha {numero}: quantidade {bruto_qtd} inválida — ignorada.')
                continue
            quantidade = int(numero_qtd)
        tamanho = limpar_tamanho(valor(linha, 'tamanho'))
        try:
            minimo = int(float(str(valor(linha, 'minimo') or 0).replace(',', '.')))
        except ValueError:
            minimo = 0
        chave = chave_item(tipo, tamanho, descricao)
        if chave in juntas:
            juntas[chave]['quantidade'] += quantidade
            avisos.append(f'Linha {numero}: {tipo} {tamanho} repetido — somado à linha anterior.')
            continue
        existente = existentes.get(chave)
        juntas[chave] = {
            'tipo': tipo, 'tamanho': tamanho, 'descricao': descricao, 'quantidade': quantidade,
            'minimo': max(0, minimo), 'item_id': existente.id if existente else None,
            'saldo_atual': existente.saldo if existente else 0,
        }
    if not juntas:
        raise ErroEstoque('A planilha não tem nenhuma linha de uniforme para importar.')
    return {'linhas': list(juntas.values()), 'avisos': avisos,
            'colunas': {campo: str(brutas[indice][pos]) for campo, pos in mapa.items()}}


@transaction.atomic
def aplicar(linhas, *, modo, por, nome_arquivo=''):
    """Cria os itens que faltam e lança. Tudo ou nada."""
    origem = f'Importação da planilha {nome_arquivo}'.strip()
    resumo = {'itens_novos': 0, 'entradas': 0, 'ajustes': 0, 'pecas': 0, 'sem_mudanca': 0}
    for linha in linhas:
        item, novo = servicos.criar_item(tipo=linha['tipo'], tamanho=linha['tamanho'],
                                         descricao=linha['descricao'],
                                         estoque_minimo=linha.get('minimo') or 0, por=por)
        if novo:
            resumo['itens_novos'] += 1
        elif linha.get('minimo') and item.estoque_minimo != linha['minimo']:
            item.estoque_minimo = linha['minimo']
            item.save(update_fields=['estoque_minimo', 'atualizado_em'])
        qtd = int(linha['quantidade'] or 0)
        if modo == MODO_ACERTAR:
            item.refresh_from_db(fields=['saldo'])
            if item.saldo == qtd:
                resumo['sem_mudanca'] += 1
                continue
            servicos.registrar_ajuste(item_id=item.id, contagem=qtd, por=por,
                                      motivo=f'{origem} (acerto de inventário)')
            resumo['ajustes'] += 1
            resumo['pecas'] += qtd
        elif qtd > 0:
            servicos.registrar_entrada(item_id=item.id, quantidade=qtd, por=por, observacao=origem)
            resumo['entradas'] += 1
            resumo['pecas'] += qtd
        else:
            resumo['sem_mudanca'] += 1
    return resumo


# ---------------------------------------------------------------------------
# Exportações
# ---------------------------------------------------------------------------
def _livro(titulo, cabecalho, linhas, larguras):
    from openpyxl.styles import Font, PatternFill
    livro = openpyxl.Workbook()
    aba = livro.active
    aba.title = titulo[:31]
    aba.append(cabecalho)
    for celula in aba[1]:
        celula.font = Font(bold=True, color='FFFFFF')
        celula.fill = PatternFill('solid', fgColor='4F46E5')
    for linha in linhas:
        aba.append(linha)
    for pos, largura in enumerate(larguras, start=1):
        aba.column_dimensions[openpyxl.utils.get_column_letter(pos)].width = largura
    aba.freeze_panes = 'A2'
    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()


def exportar_estoque(itens, em_posse):
    linhas = [[i.tipo, i.tamanho, i.descricao, i.saldo, em_posse.get(i.id, 0), i.estoque_minimo,
               'Sim' if i.abaixo_do_minimo else '', 'Sim' if i.ativo else 'Não'] for i in itens]
    return _livro('Estoque', ['Tipo', 'Tamanho', 'Descrição', 'Quantidade', 'Com colaboradores',
                              'Estoque mínimo', 'Abaixo do mínimo', 'Ativo'],
                  linhas, [18, 10, 30, 12, 18, 15, 16, 8])


def _nome(user):
    return (user.get_full_name() or user.email) if user else ''


def exportar_historico(movimentos):
    linhas = []
    for m in movimentos:
        linhas.append([
            timezone.localtime(m.registrado_em).strftime('%d/%m/%Y %H:%M'),
            m.rotulo_curto, m.item.tipo, m.item.tamanho, m.item.descricao, m.quantidade, m.delta,
            m.saldo_apos, _nome(m.colaborador),
            (m.colaborador.sector.name if m.colaborador and m.colaborador.sector else ''),
            m.data.strftime('%d/%m/%Y') if m.data else '', _nome(m.registrado_por),
            m.nota_fiscal, m.observacao,
        ])
    return _livro('Movimentos', ['Lançado em', 'Movimento', 'Tipo', 'Tamanho', 'Descrição', 'Quantidade',
                                 'Efeito no estoque', 'Saldo depois', 'Para quem', 'Loja/setor',
                                 'Data (entrega/recebimento/devolução)', 'Lançado por', 'Nota fiscal',
                                 'Observação'],
                  linhas, [17, 11, 14, 9, 22, 11, 15, 12, 28, 20, 18, 26, 14, 40])
