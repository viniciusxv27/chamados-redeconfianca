"""Padrão de avaliação calculado pelas avarias sinalizadas no checklist.

Os critérios são os da própria tabela de avaliação (conteudo.PADROES_AVALIACAO):
- A: sem riscos aparentes, todas as funções, bateria acima de 85%, sem trincos;
- B: leves marcas e pequenos riscos, todas as funções, bateria de 80% a 85%, sem trincos;
- C: marcas visíveis e riscos evidentes, pequenos amassados, todas as funções,
  bateria de 70% a 79%, sem trinco na tela (pode ter na carcaça);
- D: trincos, amassados evidentes, marcas acentuadas, bateria abaixo de 70%,
  falhas em funcionalidades.

Cada avaria puxa o aparelho para um padrão e vale o pior. As regras são dados
(``REGRAS``): a tela recebe o mesmo dicionário e aplica exatamente as mesmas, para
o vendedor ver o preço antes de concluir.
"""
from . import checklist

ORDEM = 'ABCD'
ITEM_TRINCOS = 'trincos'

REGRAS = {
    # Saúde da bateria: a partir de quantos % cada padrão ainda vale (do melhor para o pior).
    'bateria': [[86, 'A'], [80, 'B'], [70, 'C'], [0, 'D']],
    # Funcionalidade com observação ainda funciona; a que não funciona é falha — só cabe no D.
    'funcionalidades': {'OBS': 'B', 'NAO': 'D'},
    # Estética: observação = leves marcas / pequenos riscos; "não OK" = marcas visíveis / riscos evidentes.
    'estetica': {'OBS': 'B', 'NAO': 'C'},
    # Trincos e quebras: observação (trinco na carcaça) vai a C; "não OK", a D.
    'trincos': {'OBS': 'C', 'NAO': 'D'},
    # Com tantos itens estéticos "não OK", as marcas já são acentuadas: D.
    'estetica_nao_ok_para_d': 3,
}


def pior(letras):
    return max(letras, key=ORDEM.index) if letras else 'A'


def calcular_padrao(funcionalidades, estetica, saude_bateria):
    """(letra, motivos) — o padrão que as avarias dão ao aparelho e por quê, do pior para o melhor."""
    letras, motivos = ['A'], []

    if saude_bateria is not None:
        letra = next(l for minimo, l in REGRAS['bateria'] if saude_bateria >= minimo)
        letras.append(letra)
        if letra != 'A':
            motivos.append({'letra': letra, 'texto': f'Bateria em {saude_bateria}%'})

    titulos = {chave: titulo for chave, titulo, _, _ in checklist.FUNCIONALIDADES}
    for chave, valor in (funcionalidades or {}).items():
        letra = REGRAS['funcionalidades'].get(valor)
        if letra:
            letras.append(letra)
            motivos.append({'letra': letra,
                            'texto': f"{titulos.get(chave, chave)} {'não funciona' if valor == 'NAO' else 'com observação'}"})

    titulos = {chave: titulo for chave, titulo, _, _ in checklist.ESTETICA}
    nao_ok = 0
    for chave, valor in (estetica or {}).items():
        letra = (REGRAS['trincos'] if chave == ITEM_TRINCOS else REGRAS['estetica']).get(valor)
        if not letra:
            continue
        if chave != ITEM_TRINCOS and valor == 'NAO':
            nao_ok += 1
        letras.append(letra)
        motivos.append({'letra': letra,
                        'texto': f"{titulos.get(chave, chave)} {'não OK' if valor == 'NAO' else 'com observação'}"})
    if nao_ok >= REGRAS['estetica_nao_ok_para_d']:
        letras.append('D')
        motivos.append({'letra': 'D', 'texto': f'{nao_ok} itens estéticos não OK (marcas acentuadas)'})

    motivos.sort(key=lambda m: ORDEM.index(m['letra']), reverse=True)
    return pior(letras), motivos


def regras_para_tela():
    """O que a tela precisa para calcular igual: as regras e o nome de cada item."""
    return {
        'regras': REGRAS,
        'item_trincos': ITEM_TRINCOS,
        'titulos': {chave: titulo for chave, titulo, _, _ in checklist.FUNCIONALIDADES + checklist.ESTETICA},
    }
