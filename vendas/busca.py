"""Busca de produtos da tabela de preços (tela de venda e tabela de preços).

O `icontains` do nome achava só o texto exato, na ordem: "galaxy a15 128" não
achava "SAMSUNG GALAXY A15 128GB", "eletronico" não achava "Eletrônico" e uma
busca por "iphone 16" trazia as 30 capas antes do aparelho.

Aqui cada palavra digitada precisa aparecer (em qualquer ordem, sem acento,
maiúscula ou minúscula) no nome, na marca, na categoria ou no SKU. A ordem dá
preferência a quem começa pelo que foi digitado, aos aparelhos e conectados e
aos produtos em linha — e empurra capa/película para baixo, a menos que a
pessoa tenha pedido por elas.

A tabela tem ~2 mil itens: o índice fica na memória do processo e é refeito
quando a tabela muda (importação ou edição).
"""
import re
import unicodedata

from django.core.cache import caches
from django.db.models import Count, Max

from .models import ItemPreco

ACESSORIOS = {'capa', 'capinha', 'case', 'pelicula', 'cabo', 'carregador', 'fone', 'suporte', 'adaptador', 'kit'}


def normalizar(texto):
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    texto = ''.join(c for c in texto if not unicodedata.combining(c)).lower()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', texto).split())


def palavras(texto):
    """'Galaxy A15 128GB' → ['galaxy', 'a15', '128gb']; e '128 gb' casa com '128gb'."""
    return normalizar(texto).split()


def _assinatura():
    agregado = ItemPreco.objects.aggregate(n=Count('id'), ultimo=Max('id'), mudou=Max('updated_at'))
    return f"{agregado['n']}-{agregado['ultimo']}-{agregado['mudou']}"


def _indice():
    memoria = caches['local']
    assinatura = _assinatura()
    guardado = memoria.get('vendas_busca_indice')
    if guardado and guardado[0] == assinatura:
        return guardado[1]
    from . import slv
    linhas = []
    for item in ItemPreco.objects.filter(ativo=True).exclude(categoria='PLANOS'):
        extra = item.extra if isinstance(item.extra, dict) else {}
        nome = normalizar(item.nome)
        resto = normalizar(' '.join(str(x) for x in (
            extra.get('MARCA', ''), extra.get('FABRICANTE', ''), extra.get('CATEGORIA', ''),
            item.cod_sap, item.cod_sistema, extra.get('ID DPGC', ''), item.plano)))
        linhas.append({
            'id': item.id, 'nome': nome, 'texto': f'{nome} {resto}', 'compacto': (nome + resto).replace(' ', ''),
            'categoria_slv': slv.categoria_slv(item), 'categoria': item.categoria,
            'portfolio': str(extra.get('PORTFÓLIO', '')), 'tamanho': len(nome),
        })
    memoria.set('vendas_busca_indice', (assinatura, linhas), 600)
    return linhas


def _casa(palavra, linha):
    if palavra in linha['texto']:
        return True
    # "128 gb" digitado junto ("128gb") ou separado do nome
    return len(palavra) >= 3 and palavra in linha['compacto']


def buscar(consulta, limite=30, categoria=None):
    """Os ids dos produtos que casam com a busca, do melhor para o pior."""
    termos = palavras(consulta)
    if not termos:
        return []
    frase = ' '.join(termos)
    quer_acessorio = any(t in ACESSORIOS for t in termos)
    resultado = []
    for linha in _indice():
        if categoria and linha['categoria'] != categoria:
            continue
        if not all(_casa(t, linha) for t in termos):
            continue
        nota = 0
        if linha['nome'].startswith(frase):
            nota += 100
        elif frase in linha['nome']:
            nota += 60
        inicios = sum(1 for t in termos if re.search(r'(^| )' + re.escape(t), linha['nome']))
        nota += 10 * inicios
        if linha['categoria_slv'] == 'aparelho':
            nota += 30
        elif linha['categoria'] == 'ELETRÔNICOS_LP_Conectados':
            nota += 20
        if linha['portfolio'] == 'In':
            nota += 8
        elif linha['portfolio'] in ('Out', 'EOL'):
            nota -= 8
        if not quer_acessorio and linha['categoria_slv'] == 'essencial':
            nota -= 45
        nota -= linha['tamanho'] / 40          # nome mais curto (o próprio produto) antes das variações
        resultado.append((nota, linha['nome'], linha['id']))
    resultado.sort(key=lambda r: (-r[0], r[1]))
    return [r[2] for r in resultado[:limite]]
