"""Leitura e validação do checklist enviado pela tela.

Função pura sobre o POST: devolve os dados prontos para o ``Renova`` e os erros
por campo, para a tela reabrir o formulário preenchido e apontar o que falta.
"""
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_date

from . import checklist
from .padrao import calcular_padrao

PREFIXO_ASSINATURA = 'data:image/png;base64,'
ASSINATURA_MAX = 400_000      # caracteres do data URL da assinatura
NUMERO_VENDA_MAX = 40         # o max_length de Renova.numero_venda


def imei_valido(imei):
    """15 números com o dígito verificador (Luhn) certo — pega IMEI digitado errado."""
    if not re.fullmatch(r'\d{15}', imei or ''):
        return False
    soma = 0
    for posicao, digito in enumerate(int(c) for c in imei):
        if posicao % 2 == 1:
            digito *= 2
            if digito > 9:
                digito -= 9
        soma += digito
    return soma % 10 == 0


def _texto(post, campo, limite):
    return ' '.join(str(post.get(campo, '') or '').split())[:limite]


def _data(post, campo):
    try:
        return parse_date(str(post.get(campo, '') or '').strip())
    except ValueError:
        return None


def ler_valor(texto):
    """'1920', '1920.50' (campo numérico) ou '1.920,50' → Decimal; vazio → None."""
    bruto = str(texto or '').strip().replace('R$', '').replace(' ', '')
    if not bruto:
        return None
    if ',' in bruto:
        bruto = bruto.replace('.', '').replace(',', '.')
    try:
        valor = Decimal(bruto)
    except InvalidOperation:
        raise ValueError(texto) from None
    if valor < 0 or valor > Decimal('9999999'):
        raise ValueError(texto)
    return valor.quantize(Decimal('0.01'))


def ler_numero_venda(texto):
    """Nº da venda como a pessoa digitou, sem máscara; vazio → ''.

    Cada sistema de venda numera de um jeito (só números, com ponto, com letra),
    então não há formato a conferir: só saem os espaços sobrando e os caracteres
    invisíveis (colados de planilha ou sistema, eles não aparecem na etiqueta e
    atrapalham a busca). Mais longo que o campo: ValueError — cortar calado
    guardaria um número que não é o da venda.
    """
    visiveis = ''.join(c for c in str(texto or '') if c.isspace() or unicodedata.category(c)[0] != 'C')
    valor = ' '.join(visiveis.split())
    if len(valor) > NUMERO_VENDA_MAX:
        raise ValueError(texto)
    return valor


def normal_modelo(texto):
    """"iPhone 15  Pro" e "iphone 15 pro" viram a mesma coisa: sem acento, caixa, espaço nem "iphone"."""
    texto = unicodedata.normalize('NFD', str(texto or '').lower())
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r'\s+', '', texto.replace('iphone', ''))


def linha_da_tabela(marca, modelo, armazenamento, precos):
    """A linha da tabela de avaliação do aparelho — o mesmo casamento que a tela faz."""
    if marca != 'APPLE' or not modelo or not armazenamento:
        return None
    alvo = normal_modelo(modelo)
    return next((p for p in precos.values()
                 if p.marca == 'APPLE' and p.armazenamento == armazenamento and normal_modelo(p.modelo) == alvo), None)


def ler_checklist(post, *, lojas, precos, hoje=None, cfg=None):
    """(dados, erros) do checklist.

    ``lojas`` e ``precos`` são dicionários {id em texto: objeto} com o que a
    pessoa pode escolher — o que vier fora deles é recusado.
    """
    hoje = hoje or timezone.localdate()
    erros = {}
    d = {}

    # 1. Dados do aparelho — só Apple, com modelo e armazenamento da tabela de avaliação
    d['marca'], d['marca_outra'], d['armazenamento_outro'], d['numero_serie'] = 'APPLE', '', '', ''
    d['modelo'] = _texto(post, 'modelo', 120)
    if not d['modelo']:
        erros['modelo'] = 'Escolha o modelo.'
    d['cor'] = _texto(post, 'cor', 60)
    d['armazenamento'] = str(post.get('armazenamento', '') or '')[:10]
    if not d['armazenamento']:
        erros['armazenamento'] = 'Escolha o armazenamento.'
    d['preco_tabela'] = linha_da_tabela('APPLE', d['modelo'], d['armazenamento'], precos)
    if d['preco_tabela'] is None and not ({'modelo', 'armazenamento'} & erros.keys()):
        erros['modelo'] = ('Esse modelo com esse armazenamento não está na tabela de avaliação — '
                           'peça ao SUPERADMIN para incluir.')

    d['imei1'] = re.sub(r'\D', '', str(post.get('imei1', '')))[:15]
    if not imei_valido(d['imei1']):
        erros['imei1'] = 'IMEI inválido: são 15 números (disque *#06# ou veja em Ajustes > Geral > Sobre).'
    d['imei2'] = re.sub(r'\D', '', str(post.get('imei2', '')))[:15]
    if d['imei2'] and not imei_valido(d['imei2']):
        erros['imei2'] = 'IMEI 2 inválido: são 15 números.'
    elif d['imei2'] and d['imei2'] == d['imei1']:
        erros['imei2'] = 'O IMEI 2 está igual ao IMEI 1.'

    d['data_avaliacao'] = _data(post, 'data_avaliacao')
    if d['data_avaliacao'] is None:
        erros['data_avaliacao'] = 'Informe a data da avaliação.'
    elif d['data_avaliacao'] > hoje:
        erros['data_avaliacao'] = 'A data da avaliação não pode ser no futuro.'

    d['loja'] = lojas.get(str(post.get('loja', '')))
    if d['loja'] is None:
        erros['loja'] = 'Escolha a loja de origem.'

    bateria = str(post.get('saude_bateria', '') or '').strip().rstrip('%')
    d['saude_bateria'] = None
    if not bateria:
        erros['saude_bateria'] = 'Informe a saúde da bateria (Ajustes > Bateria > Saúde da bateria).'
    elif bateria.isdigit() and 0 <= int(bateria) <= 100:
        d['saude_bateria'] = int(bateria)
    else:
        erros['saude_bateria'] = 'Saúde da bateria vai de 0 a 100%.'

    # 2. Itens obrigatórios antes da avaliação — todos
    d['itens_obrigatorios'] = {chave: post.get(f'obrig_{chave}') == 'on'
                               for chave, _, _, _ in checklist.ITENS_OBRIGATORIOS}
    faltando = [titulo for chave, titulo, _, _ in checklist.ITENS_OBRIGATORIOS if not d['itens_obrigatorios'][chave]]
    if faltando:
        erros['itens_obrigatorios'] = 'Confira todos os itens obrigatórios antes da avaliação: ' + '; '.join(faltando) + '.'

    # 3 e 4. Funcionalidades e condição estética — uma resposta por item
    for campo, prefixo, itens, opcoes, rotulo in (
            ('funcionalidades', 'func', checklist.FUNCIONALIDADES, checklist.OPCOES_FUNCIONALIDADE, 'todas as funcionalidades'),
            ('estetica', 'est', checklist.ESTETICA, checklist.OPCOES_ESTETICA, 'todos os itens da condição estética')):
        validas = dict(opcoes)
        d[campo] = {chave: str(post.get(f'{prefixo}_{chave}', '')) for chave, _, _, _ in itens}
        sem_resposta = [titulo for chave, titulo, _, _ in itens if d[campo][chave] not in validas]
        if sem_resposta:
            erros[campo] = f'Marque {rotulo}: faltou ' + ', '.join(sem_resposta) + '.'

    # Padrão e valor de troca: saem das avarias sinalizadas e da tabela — ninguém escolhe.
    d['padrao'], d['padrao_motivos'], d['valor_estimado'] = '', [], None
    if not ({'funcionalidades', 'estetica', 'saude_bateria'} & erros.keys()):
        d['padrao'], d['padrao_motivos'] = calcular_padrao(d['funcionalidades'], d['estetica'], d['saude_bateria'])
        if d['preco_tabela'] is not None:
            if cfg is None:
                from .models import ConfiguracaoRenova
                cfg = ConfiguracaoRenova.get()
            d['valor_estimado'] = cfg.valor_do_padrao(d['preco_tabela'].valor_excelente, d['padrao'])

    # 5. Observações. O parecer não é mais escolhido: sai do próprio checklist —
    # item com observação ou que não funciona deixa "aprovado com observações".
    d['observacoes'] = str(post.get('observacoes', '') or '').strip()[:4000]
    sinalizados = [valor for campo in ('funcionalidades', 'estetica') for valor in d[campo].values()
                   if valor in ('OBS', 'NAO')]
    d['parecer'] = checklist.APROVADO_OBS if sinalizados else checklist.APROVADO
    if sinalizados and not d['observacoes']:
        erros['observacoes'] = 'Conte nas observações o que ficou com observação ou não funciona.'

    # 6. Responsável pela avaliação
    d['vendedor_nome'] = _texto(post, 'vendedor_nome', 150)
    if not d['vendedor_nome']:
        erros['vendedor_nome'] = 'Informe o nome do vendedor.'
    d['matricula'] = _texto(post, 'matricula', 40)
    d['assinatura'] = str(post.get('assinatura', '') or '')
    if not d['assinatura'].startswith(PREFIXO_ASSINATURA) or len(d['assinatura']) < 200:
        erros['assinatura'] = 'Assine no quadro de assinatura.'
    elif len(d['assinatura']) > ASSINATURA_MAX:
        erros['assinatura'] = 'A assinatura ficou grande demais: limpe e assine de novo.'
    d['data_responsavel'] = _data(post, 'data_responsavel') or hoje

    # Nº da venda: opcional aqui — quase sempre sai depois, e entra pela tela da avaliação.
    try:
        d['numero_venda'] = ler_numero_venda(post.get('numero_venda'))
    except ValueError:
        d['numero_venda'] = ''
        erros['numero_venda'] = f'O nº da venda vai até {NUMERO_VENDA_MAX} caracteres — confira o número.'

    return d, erros
