"""Leitura e validação do checklist enviado pela tela.

Função pura sobre o POST: devolve os dados prontos para o ``Renova`` e os erros
por campo, para a tela reabrir o formulário preenchido e apontar o que falta.
"""
import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_date

from . import checklist

PREFIXO_ASSINATURA = 'data:image/png;base64,'
ASSINATURA_MAX = 400_000      # caracteres do data URL da assinatura


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


def ler_checklist(post, *, lojas, precos, hoje=None):
    """(dados, erros) do checklist.

    ``lojas`` e ``precos`` são dicionários {id em texto: objeto} com o que a
    pessoa pode escolher — o que vier fora deles é recusado.
    """
    hoje = hoje or timezone.localdate()
    erros = {}
    d = {}

    # 1. Dados do aparelho
    d['marca'] = str(post.get('marca', ''))
    if d['marca'] not in dict(checklist.MARCAS):
        erros['marca'] = 'Escolha a marca.'
    d['marca_outra'] = _texto(post, 'marca_outra', 60) if d['marca'] == 'OUTROS' else ''
    if d['marca'] == 'OUTROS' and not d['marca_outra']:
        erros['marca_outra'] = 'Informe qual é a marca.'
    d['modelo'] = _texto(post, 'modelo', 120)
    if not d['modelo']:
        erros['modelo'] = 'Informe o modelo.'
    d['cor'] = _texto(post, 'cor', 60)
    d['armazenamento'] = str(post.get('armazenamento', ''))
    if d['armazenamento'] not in dict(checklist.ARMAZENAMENTOS):
        erros['armazenamento'] = 'Escolha o armazenamento.'
    d['armazenamento_outro'] = _texto(post, 'armazenamento_outro', 20) if d['armazenamento'] == 'OUTRO' else ''
    if d['armazenamento'] == 'OUTRO' and not d['armazenamento_outro']:
        erros['armazenamento_outro'] = 'Informe o armazenamento.'

    d['imei1'] = re.sub(r'\D', '', str(post.get('imei1', '')))[:15]
    if not imei_valido(d['imei1']):
        erros['imei1'] = 'IMEI inválido: são 15 números (disque *#06# ou veja em Ajustes > Geral > Sobre).'
    d['imei2'] = re.sub(r'\D', '', str(post.get('imei2', '')))[:15]
    if d['imei2'] and not imei_valido(d['imei2']):
        erros['imei2'] = 'IMEI 2 inválido: são 15 números.'
    elif d['imei2'] and d['imei2'] == d['imei1']:
        erros['imei2'] = 'O IMEI 2 está igual ao IMEI 1.'
    d['numero_serie'] = _texto(post, 'numero_serie', 40)

    d['data_avaliacao'] = _data(post, 'data_avaliacao')
    if d['data_avaliacao'] is None:
        erros['data_avaliacao'] = 'Informe a data da avaliação.'
    elif d['data_avaliacao'] > hoje:
        erros['data_avaliacao'] = 'A data da avaliação não pode ser no futuro.'

    d['loja'] = lojas.get(str(post.get('loja', '')))
    if d['loja'] is None:
        erros['loja'] = 'Escolha a loja de origem.'

    d['padrao'] = str(post.get('padrao', ''))
    if d['padrao'] and d['padrao'] not in dict(checklist.PADROES):
        erros['padrao'] = 'Padrão inválido.'
    d['preco_tabela'] = precos.get(str(post.get('preco_tabela', '')))
    try:
        d['valor_estimado'] = ler_valor(post.get('valor_estimado'))
    except ValueError:
        d['valor_estimado'] = None
        erros['valor_estimado'] = 'Valor estimado inválido.'
    bateria = str(post.get('saude_bateria', '') or '').strip().rstrip('%')
    d['saude_bateria'] = None
    if bateria:
        if bateria.isdigit() and 0 <= int(bateria) <= 100:
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

    # 5 e 6. Observações e parecer final
    d['observacoes'] = str(post.get('observacoes', '') or '').strip()[:4000]
    d['parecer'] = str(post.get('parecer', ''))
    if d['parecer'] not in dict(checklist.PARECERES):
        erros['parecer'] = 'Escolha o parecer final do aparelho.'
    elif d['parecer'] in (checklist.APROVADO_OBS, checklist.NAO_APROVADO) and not d['observacoes']:
        erros['observacoes'] = 'Conte nas observações o motivo desse parecer.'
    if (d['parecer'] in (checklist.APROVADO, checklist.APROVADO_OBS) and d['valor_estimado'] is None
            and 'valor_estimado' not in erros):
        erros['valor_estimado'] = 'Informe o valor estimado de troca.'

    # 7. Responsável pela avaliação
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

    return d, erros
