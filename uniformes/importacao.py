"""Importação do estoque por planilha (Excel/CSV) e exportações.

Dois formatos, achados sozinhos:

1. **Conferência da rede** ("RELAÇÃO UNIFORMES - ES - ESTOQUE", aba "Conferência Uniformes",
   12/11/2026): ``UNIFORME / TAMANHO | SALDO NOVOS | SALDO USADOS | DATA AJUSTE | OBS:``.
   A 1ª coluna junta peça, linha, gênero e tamanho ("Camisa CN - Feminino - PP"): vira tipo
   "Camisa", modelo "CN Feminino" e tamanho "PP". Novos e usados são o MESMO item (14/11/2026):
   SALDO NOVOS vai para o estoque novo e SALDO USADOS para a quantidade usados, cada um com o
   seu lançamento (condição NOVO/USADO). A data e o "OBS" (quem conferiu) vão para o lançamento. Linhas de seção ("UNIFORMES FEMININOS") e o quadro de
   totais à direita não são itens. Por ser conferência, a tela sugere acertar o saldo.
2. **Genérico**: acha a linha do cabeçalho nas 15 primeiras, aceita sinônimos ("Peça", "Qtd",
   "Tam.", "Modelo"…), ignora maiúsculas e acentos. A quantidade vai para o estoque novo (um
   modelo terminado em "Usado" vai para os usados do item sem o sufixo).

Nos dois, linhas repetidas do mesmo item são somadas e nada grava na leitura: a tela mostra a
prévia e só o "Confirmar" lança.
"""
from __future__ import annotations

import csv
import io
import re
from collections import OrderedDict
from datetime import date, datetime

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
                   'quantidade em estoque', 'qtd estoque', 'total', 'estoque novo', 'novos', 'quantidade novos',
                   'qtd novos', 'saldo novo'],
    # 14/11/2026: a exportação do estoque traz "Estoque novo" e "Quantidade usados" lado a lado.
    'usados': ['quantidade usados', 'usados', 'qtd usados', 'qtde usados', 'saldo usado', 'estoque usado'],
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
        if _achar_cabecalho_conferencia(linhas)[0] is not None or _achar_cabecalho(linhas)[0] is not None:
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


# ---------------------------------------------------------------------------
# Formato "Conferência Uniformes" (planilha da rede)
# ---------------------------------------------------------------------------
FORMATO_CONFERENCIA = 'conferencia'
FORMATO_GENERICO = 'generico'

CONFERENCIA = {
    'nome': ('uniforme / tamanho', 'uniforme/tamanho', 'uniforme - tamanho', 'uniforme e tamanho'),
    'novos': ('saldo novos', 'saldo novo', 'novos', 'qtd novos'),
    'usados': ('saldo usados', 'saldo usado', 'usados', 'qtd usados'),
    'data': ('data ajuste', 'data do ajuste', 'data da conferencia', 'data conferencia', 'data'),
    'obs': ('obs', 'obs.', 'observacao', 'conferido por'),
}

# "Camisa CN - Feminino - PP": hífen com espaço em volta (ou travessão) separa as partes.
SEPARADOR_NOME = re.compile(r'\s+[-\u2013\u2014]\s+|\s*[\u2013\u2014]\s*')
TAMANHO_LETRA = re.compile(r'(PP|P|M|G|GG|XG|XGG|EG|EGG|EXG|EXGG|[GX]\d|EG\d|XG\d|G\d\d?|U|UN|UNICO|TU)')


def _parece_tamanho(texto) -> bool:
    texto = norm(texto).upper().replace(' ', '')
    return bool(texto) and (texto.isdigit() and len(texto) <= 3 or bool(TAMANHO_LETRA.fullmatch(texto)))


def separar_nome(texto):
    """"Camisa CN - Feminino - PP" → ("Camisa", "CN Feminino", "PP").

    A peça é a 1ª palavra; o resto do 1º pedaço (a linha: CN, GER…) e os pedaços do meio (gênero)
    viram o modelo; o último pedaço é o tamanho quando parece tamanho. Sem separador, o texto
    inteiro é a peça.
    """
    texto = ' '.join(str(texto or '').split())
    partes = [p.strip() for p in SEPARADOR_NOME.split(texto) if p and p.strip()]
    if not partes:
        return '', '', ''
    tamanho = ''
    if len(partes) >= 2 and _parece_tamanho(partes[-1]):
        tamanho = partes.pop()
    palavras = partes[0].split()
    tipo = palavras[0]
    modelo = ' '.join(palavras[1:] + partes[1:])
    return limpar_tipo(tipo), limpar_descricao(modelo), limpar_tamanho(tamanho)


def _campo_conferencia(cabecalho):
    texto = norm(cabecalho).rstrip(':').strip()
    for campo, nomes in CONFERENCIA.items():
        if texto in nomes:
            return campo
    return None


def _achar_cabecalho_conferencia(linhas):
    for indice, linha in enumerate(linhas[:15]):
        mapa = {}
        for pos, valor in enumerate(linha):
            campo = _campo_conferencia(valor) if valor is not None else None
            if campo and campo not in mapa:
                mapa[campo] = pos
        if 'nome' in mapa and ('novos' in mapa or 'usados' in mapa):
            return indice, mapa
    return None, {}


def _quantidade(bruto):
    """Inteiro >= 0, None se vazio, ou levanta ValueError."""
    if bruto is None or str(bruto).strip() in ('', '-'):
        return None
    numero = float(str(bruto).strip().replace(',', '.'))
    try:
        inteiro = int(numero)
    except OverflowError:          # "inf", "1e400": não é quantidade (sem isso a prévia dava erro 500)
        raise ValueError(bruto) from None
    if numero < 0 or numero != inteiro:
        raise ValueError(bruto)
    return inteiro


def _data(bruto):
    """Data da conferência como 'AAAA-MM-DD' (vai para a sessão em JSON), ou ''."""
    if isinstance(bruto, datetime):
        return bruto.date().isoformat()
    if isinstance(bruto, date):
        return bruto.isoformat()
    if isinstance(bruto, (int, float)) and not isinstance(bruto, bool) and 20000 < bruto < 80000:
        try:                       # célula de data sem formato: o Excel guarda o nº de série (46330 = 04/11/2026)
            from openpyxl.utils.datetime import from_excel
            return from_excel(bruto).date().isoformat()
        except (ValueError, OverflowError, TypeError):
            return ''
    texto = str(bruto or '').strip()
    for formato in ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(texto[:10], formato).date().isoformat()
        except ValueError:
            continue
    return ''


def _ler_conferencia(brutas, indice, mapa):
    def valor(linha, campo):
        pos = mapa.get(campo)
        return linha[pos] if pos is not None and pos < len(linha) else None

    existentes = {chave_item(i.tipo, i.tamanho, i.descricao): i for i in ItemUniforme.objects.all()}
    juntas, avisos = OrderedDict(), []
    totais = {'novos': 0, 'usados': 0, 'secoes': 0}

    def juntar(numero, tipo, descricao, tamanho, novos, usados, data, obs):
        chave = chave_item(tipo, tamanho, descricao)
        if chave in juntas:
            juntas[chave]['quantidade'] += novos
            juntas[chave]['usados'] += usados
            avisos.append(f'Linha {numero}: {tipo} {descricao} {tamanho} repetido — somado à linha anterior.')
            return
        existente = existentes.get(chave)
        juntas[chave] = _linha(tipo, tamanho, descricao, novos, usados, 0, existente, data=data, obs=obs)

    for faltando, rotulo in (('novos', 'SALDO NOVOS'), ('usados', 'SALDO USADOS')):
        if faltando not in mapa:
            avisos.append(f'ATENÇÃO: não achei a coluna "{rotulo}" — esse saldo está sendo lido como 0. '
                          'Confira o título da coluna antes de acertar o saldo.')
    for numero, linha in enumerate(brutas[indice + 1:], start=indice + 2):
        nome = valor(linha, 'nome')
        if nome is None or not str(nome).strip():
            continue
        if norm(nome).startswith('total'):
            totais['secoes'] += 1          # linha de total no fim da tabela: não é peça
            continue
        bruto_novos, bruto_usados = valor(linha, 'novos'), valor(linha, 'usados')
        try:
            novos, usados = _quantidade(bruto_novos), _quantidade(bruto_usados)
        except ValueError:
            avisos.append(f'Linha {numero}: "{" ".join(str(nome).split())}" com saldo que não é número inteiro '
                          f'({bruto_novos} / {bruto_usados}) — ignorada.')
            continue
        if novos is None and usados is None:
            totais['secoes'] += 1          # "UNIFORMES FEMININOS": título de seção, não é peça
            continue
        tipo, modelo, tamanho = separar_nome(nome)
        if not tipo:
            avisos.append(f'Linha {numero}: sem nome de peça — ignorada.')
            continue
        if not tamanho:
            avisos.append(f'Linha {numero}: "{" ".join(str(nome).split())}" sem tamanho no fim do nome — '
                          'entra como tamanho único.')
        data = _data(valor(linha, 'data'))
        obs = ' '.join(str(valor(linha, 'obs') or '').split())[:60]
        novos, usados = novos or 0, usados or 0
        totais['novos'] += novos
        totais['usados'] += usados
        juntar(numero, tipo, modelo, tamanho, novos, usados, data, obs)
    if not juntas:
        raise ErroEstoque('A planilha de conferência não tem nenhuma linha de uniforme com saldo.')
    totais['pecas'] = totais['novos'] + totais['usados']
    return {'linhas': list(juntas.values()), 'avisos': avisos, 'formato': FORMATO_CONFERENCIA,
            'modo_sugerido': MODO_ACERTAR, 'totais': totais,
            'colunas': {campo: ' '.join(str(brutas[indice][pos]).split()) for campo, pos in mapa.items()}}


def _linha(tipo, tamanho, descricao, novos, usados, minimo, existente, data='', obs=''):
    """Uma linha da prévia: um item, com o que a planilha diz de novos e de usados.

    `quantidade` (novos) ou `usados` None = a planilha não fala daquela condição (formato genérico
    só com novos, ou só com linhas "… Usado"): o saldo dela não é tocado.
    """
    return {
        'tipo': tipo, 'tamanho': tamanho, 'descricao': descricao, 'quantidade': novos, 'usados': usados,
        'minimo': minimo, 'item_id': existente.id if existente else None,
        'saldo_atual': existente.saldo if existente else 0,
        'usado_atual': existente.saldo_usado if existente else 0,
        'data': data, 'obs': obs,
    }


def ler_planilha(arquivo, nome=''):
    """Lê e devolve a prévia: {'linhas': [...], 'avisos': [...], 'colunas': {...}}.

    Cada linha: tipo, tamanho, descricao, quantidade (novos), usados (None quando a
    planilha não tem), minimo, item_id (se já existe), saldo_atual, usado_atual.
    Linhas iguais são somadas.
    """
    brutas = _linhas_brutas(arquivo, nome)
    indice, mapa = _achar_cabecalho_conferencia(brutas)
    if indice is not None:
        return _ler_conferencia(brutas, indice, mapa)
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
        quantidade = _qtd_generica(valor(linha, 'quantidade'), numero, avisos)
        if quantidade is None:
            continue
        coluna_usados = None
        if 'usados' in mapa:
            coluna_usados = _qtd_generica(valor(linha, 'usados'), numero, avisos)
            if coluna_usados is None:
                continue
        tamanho = limpar_tamanho(valor(linha, 'tamanho'))
        try:
            minimo = int(float(str(valor(linha, 'minimo') or 0).replace(',', '.')))
        except ValueError:
            minimo = 0
        # "Camisa | M | 3 | Polo · Usado": não cria item usado à parte, vai para os usados da Polo.
        # None = a planilha não fala daquela condição, e o saldo dela não é tocado.
        descricao, eh_usado = servicos.separar_usado(descricao)
        if eh_usado:
            novos, usados = None, quantidade + (coluna_usados or 0)
        else:
            novos, usados = quantidade, coluna_usados
        chave = chave_item(tipo, tamanho, descricao)
        if chave in juntas:
            linha_junta = juntas[chave]
            for campo, qtd in (('quantidade', novos), ('usados', usados)):
                if qtd is not None:
                    linha_junta[campo] = (linha_junta[campo] or 0) + qtd
            avisos.append(f'Linha {numero}: {tipo} {tamanho} repetido — somado à linha anterior.')
            continue
        juntas[chave] = _linha(tipo, tamanho, descricao, novos, usados, max(0, minimo), existentes.get(chave))
    if not juntas:
        raise ErroEstoque('A planilha não tem nenhuma linha de uniforme para importar.')
    modo = MODO_ENTRADA
    if any(norm(c) == 'com colaboradores' for c in brutas[indice] if c is not None):
        # Já aconteceu (13/11/2026): a exportação reimportada como entrada dobrou o estoque.
        modo = MODO_ACERTAR
        avisos.insert(0, 'ATENÇÃO: esta parece a exportação do próprio estoque. Somar como entrada DOBRA o estoque; '
                         'para corrigir contagens, use "Acertar o saldo".')
    return {'linhas': list(juntas.values()), 'avisos': avisos, 'formato': FORMATO_GENERICO,
            'modo_sugerido': modo,
            'colunas': {campo: str(brutas[indice][pos]) for campo, pos in mapa.items()}}


def _qtd_generica(bruto, numero, avisos):
    """Quantidade de uma célula do formato genérico: 0 se vazia, inteiro >= 0, ou None (com aviso)."""
    if bruto is None or str(bruto).strip() in ('', '-'):
        return 0
    try:
        numero_qtd = float(str(bruto).strip().replace(',', '.'))
        int(numero_qtd)
    except (ValueError, OverflowError):
        avisos.append(f'Linha {numero}: quantidade "{bruto}" não é número — ignorada.')
        return None
    if numero_qtd < 0 or numero_qtd != int(numero_qtd):
        avisos.append(f'Linha {numero}: quantidade {bruto} inválida — ignorada.')
        return None
    return int(numero_qtd)


@transaction.atomic
def aplicar(linhas, *, modo, por, nome_arquivo=''):
    """Cria os itens que faltam e lança, cada condição no seu saldo. Tudo ou nada.

    Novos vão para o estoque novo e usados para a quantidade usados do mesmo item;
    quantidade None (a planilha não falou daquela condição) não toca o saldo dela.
    `sem_mudanca` conta itens em que nada foi lançado.
    """
    origem = f'Importação da planilha {nome_arquivo}'.strip()
    resumo = {'itens_novos': 0, 'entradas': 0, 'ajustes': 0, 'pecas': 0, 'sem_mudanca': 0}
    for linha in linhas:
        data = _data(linha.get('data')) if linha.get('data') else ''
        dia = date.fromisoformat(data) if data else None
        detalhe = origem
        if dia or linha.get('obs'):
            detalhe += ' — conferência' + (f' de {dia:%d/%m/%Y}' if dia else '') + (
                f' por {linha["obs"]}' if linha.get('obs') else '')
        item, novo = servicos.criar_item(tipo=linha['tipo'], tamanho=linha['tamanho'],
                                         descricao=linha['descricao'],
                                         estoque_minimo=linha.get('minimo') or 0, por=por)
        if novo:
            resumo['itens_novos'] += 1
        elif linha.get('minimo') and item.estoque_minimo != linha['minimo']:
            item.estoque_minimo = linha['minimo']
            item.save(update_fields=['estoque_minimo', 'atualizado_em'])
        lancou = False
        for condicao, chave in ((servicos.NOVO, 'quantidade'), (servicos.USADO, 'usados')):
            if linha.get(chave) is None:
                continue
            qtd = int(linha[chave] or 0)
            if modo == MODO_ACERTAR:
                item.refresh_from_db(fields=['saldo', 'saldo_usado'])
                if item.saldo_de(condicao) == qtd:
                    continue
                servicos.registrar_ajuste(item_id=item.id, contagem=qtd, por=por, data=dia, condicao=condicao,
                                          motivo=f'{detalhe} (acerto de inventário)')
                resumo['ajustes'] += 1
            elif qtd > 0:
                servicos.registrar_entrada(item_id=item.id, quantidade=qtd, por=por, data=dia,
                                           condicao=condicao, observacao=detalhe)
                resumo['entradas'] += 1
            else:
                continue
            resumo['pecas'] += qtd
            lancou = True
        if not lancou:
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


COLUNAS_ESTOQUE = ['Tipo', 'Tamanho', 'Descrição', 'Estoque novo', 'Quantidade usados', 'Com colaboradores',
                   'Total em estoque (novo + usados)', 'Estoque mínimo', 'Abaixo do mínimo', 'Ativo']


def exportar_estoque(itens, em_posse):
    linhas = [[i.tipo, i.tamanho, i.descricao, i.saldo, i.saldo_usado, em_posse.get(i.id, 0), i.total_estoque,
               i.estoque_minimo, 'Sim' if i.abaixo_do_minimo else '', 'Sim' if i.ativo else 'Não'] for i in itens]
    return _livro('Estoque', COLUNAS_ESTOQUE, linhas, [18, 10, 30, 13, 18, 18, 18, 15, 16, 8])


def _nome(user):
    return (user.get_full_name() or user.email) if user else ''


def exportar_historico(movimentos):
    linhas = []
    for m in movimentos:
        linhas.append([
            timezone.localtime(m.registrado_em).strftime('%d/%m/%Y %H:%M'),
            m.rotulo_curto, m.item.tipo, m.item.tamanho, m.item.descricao,
            'Usado' if m.usado else 'Novo', m.quantidade, m.delta, m.saldo_apos, _nome(m.colaborador),
            (m.colaborador.sector.name if m.colaborador and m.colaborador.sector else ''),
            m.data.strftime('%d/%m/%Y') if m.data else '', _nome(m.registrado_por),
            m.nota_fiscal, m.observacao,
        ])
    return _livro('Movimentos', ['Lançado em', 'Movimento', 'Tipo', 'Tamanho', 'Descrição', 'Condição',
                                 'Quantidade', 'Efeito no estoque', 'Saldo depois (da condição)', 'Para quem',
                                 'Loja/setor', 'Data (entrega/recebimento/devolução)', 'Lançado por',
                                 'Nota fiscal', 'Observação'],
                  linhas, [17, 11, 14, 9, 22, 10, 11, 15, 16, 28, 20, 18, 26, 14, 40])
