"""O checklist de avaliação do aparelho — o mesmo do impresso do Vini Renova.

Uma fonte só para o formulário, a validação, o chamado, o detalhe e a etiqueta:
mudar um item aqui muda todos os lugares. As respostas ficam em JSON no
``Renova`` (chave do item → valor), então item novo não pede migração.

Cada item: (chave, título, descrição, ícone Font Awesome).
"""

MARCAS = [
    ('APPLE', 'Apple'),
    ('SAMSUNG', 'Samsung'),
    ('MOTOROLA', 'Motorola'),
    ('XIAOMI', 'Xiaomi'),
    ('OUTROS', 'Outros'),
]

ARMAZENAMENTOS = [
    ('64GB', '64GB'),
    ('128GB', '128GB'),
    ('256GB', '256GB'),
    ('512GB', '512GB'),
    ('1TB', '1TB'),
    ('OUTRO', 'Outro'),
]

# 2. Itens obrigatórios antes da avaliação — todos precisam estar conferidos.
ITENS_OBRIGATORIOS = [
    ('capa', 'Retirar capa, película e acessórios', 'Avaliar o aparelho sem itens adicionais.', 'fa-solid fa-layer-group'),
    ('backup', 'Verificar se o cliente realizou backup', 'Orientar o cliente, se necessário.', 'fa-solid fa-cloud'),
    ('conta', 'Sair da conta iCloud / Google', 'Aparelho deve estar sem contas vinculadas.', 'fa-solid fa-lock'),
    ('fabrica', 'Restaurar o aparelho para o padrão de fábrica',
     'Conferir se não há bloqueios (iCloud, Google, operadora, MDM).', 'fa-solid fa-gear'),
    ('carregador', 'Testar carregador e cabo', 'Verificar funcionamento.', 'fa-solid fa-plug'),
    ('chip', 'Conferir se o chip foi removido', 'Não deixar chip no aparelho.', 'fa-solid fa-sim-card'),
]

# 3. Funcionalidades
OPCOES_FUNCIONALIDADE = [('OK', 'OK'), ('OBS', 'Com observação'), ('NAO', 'Não funciona')]
FUNCIONALIDADES = [
    ('liga_desliga', 'Liga e desliga', 'Aparelho liga normalmente.', 'fa-solid fa-power-off'),
    ('bateria', 'Bateria', 'Saúde da bateria (ajuste nos parâmetros da Vivo).', 'fa-solid fa-battery-three-quarters'),
    ('rede', 'Rede celular', 'Sinal e chip.', 'fa-solid fa-signal'),
    ('wifi', 'Wi-Fi', 'Conecta normalmente.', 'fa-solid fa-wifi'),
    ('bluetooth', 'Bluetooth', 'Conecta normalmente.', 'fa-brands fa-bluetooth-b'),
    ('cameras', 'Câmeras', 'Frontal e traseira.', 'fa-solid fa-camera'),
    ('microfone', 'Microfone', 'Testar gravação.', 'fa-solid fa-microphone'),
    ('audio', 'Áudio', 'Alto-falantes e fone.', 'fa-solid fa-volume-high'),
]

# 4. Condição estética
OPCOES_ESTETICA = [('OK', 'OK'), ('OBS', 'Com observação'), ('NAO', 'Não OK')]
ESTETICA = [
    ('tela', 'Tela', 'Riscos, trincos, manchas, toque e brilho.', 'fa-solid fa-mobile-screen-button'),
    ('traseira', 'Traseira', 'Riscos, trincos, amassados.', 'fa-solid fa-mobile'),
    ('laterais', 'Laterais', 'Riscos, amassados, marcas de uso.', 'fa-solid fa-grip-lines-vertical'),
    ('cameras_lentes', 'Câmeras', 'Lentes, foco, qualidade.', 'fa-solid fa-camera-retro'),
    ('botoes', 'Botões', 'Power, volume, botão de ação.', 'fa-solid fa-toggle-on'),
    ('marcas_uso', 'Marcas de uso geral', 'Riscos, amassados, desgaste natural.', 'fa-solid fa-pen'),
    ('trincos', 'Trincos / Quebras', 'Tela, traseira, laterais.', 'fa-solid fa-triangle-exclamation'),
]

# 6. Parecer final
APROVADO = 'APROVADO'
APROVADO_OBS = 'APROVADO_OBS'
NAO_APROVADO = 'NAO_APROVADO'
PARECERES = [
    (APROVADO, 'Aprovado para troca'),
    (APROVADO_OBS, 'Aprovado com observações'),
    (NAO_APROVADO, 'Não aprovado'),
]

# Padrões da tabela de avaliação (A/B/C/D).
PADROES = [
    ('A', 'Excelente'),
    ('B', 'Bom'),
    ('C', 'Regular'),
    ('D', 'Abaixo do padrão'),
]


def rotulo(opcoes, valor, padrao='—'):
    return dict(opcoes).get(valor, padrao)


def respostas_de_itens(itens, respostas, opcoes):
    """[(chave, título, descrição, ícone, valor, rótulo)] na ordem do impresso."""
    respostas = respostas or {}
    return [(chave, titulo, descricao, icone, respostas.get(chave), rotulo(opcoes, respostas.get(chave)))
            for chave, titulo, descricao, icone in itens]
