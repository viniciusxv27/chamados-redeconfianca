"""Textos da tabela de avaliação e do passo a passo do Vini Renova.

São os mesmos dos impressos (static/renova/*.jpg), para a tela mostrar em HTML
de verdade — legível no celular, pesquisável e fácil de atualizar.
"""

# Padrões de avaliação (A/B/C/D): letra, nome, cor, critérios.
PADROES_AVALIACAO = [
    {'letra': 'A', 'nome': 'Excelente', 'cor': 'verde', 'criterios': [
        'Aparelho muito bem conservado',
        'Sem riscos aparentes',
        'Todas as funções funcionando',
        'Bateria acima de 85%',
        'Sem trincos ou amassados',
    ]},
    {'letra': 'B', 'nome': 'Bom', 'cor': 'azul', 'criterios': [
        'Leves marcas de uso',
        'Pequenos riscos na tela ou carcaça',
        'Todas as funções funcionando',
        'Bateria entre 80% e 85%',
        'Sem trincos',
    ]},
    {'letra': 'C', 'nome': 'Regular', 'cor': 'laranja', 'criterios': [
        'Marcas de uso visíveis',
        'Riscos mais evidentes',
        'Pode ter pequenos amassados',
        'Todas as funções funcionando',
        'Bateria entre 70% e 79%',
        'Sem trincos na tela (pode ter na carcaça)',
    ]},
    {'letra': 'D', 'nome': 'Abaixo do padrão', 'cor': 'vermelho', 'criterios': [
        'Trincos na tela e/ou carcaça',
        'Amassados evidentes',
        'Marcas de uso acentuadas',
        'Bateria abaixo de 70%',
        'Pode apresentar falhas em funcionalidades (ex.: câmera, áudio, botão etc.)',
    ]},
]

# Passo a passo da avaliação — guia prático para o vendedor.
# Cada passo: título, ícone, itens e (opcional) alerta, lista numerada ou faixas.
PASSO_A_PASSO = [
    {'chave': 'acessorios', 'titulo': 'Remover capa, película e acessórios',
     'icone': 'fa-solid fa-layer-group', 'itens': [
        'Retire capa, película, filmes e acessórios para avaliar o aparelho corretamente.',
    ]},
    {'chave': 'icloud', 'titulo': 'Verificar e remover o iCloud do cliente',
     'icone': 'fa-brands fa-apple', 'numerado': True, 'itens': [
        'Acesse Ajustes e toque no nome do cliente.',
        'Vá em Buscar > Buscar iPhone.',
        'Desative o Buscar iPhone.',
        'Volte e toque em Finalizar Sessão.',
        'Confirme que não há mais conta vinculada.',
    ], 'alerta': 'Nunca aceite o aparelho com iCloud ativo!'},
    {'chave': 'pecas', 'titulo': 'Verificar se teve peça trocada',
     'icone': 'fa-solid fa-screwdriver-wrench', 'itens': [
        'Acesse Ajustes > Geral > Sobre.',
        'Verifique em “Histórico de Peças e Serviço”.',
        'Se aparecer “Peça Original Apple” = ok.',
        'Se aparecer “Peça Desconhecida” ou “Não foi possível verificar” = sinalize e aplique o desconto da avaliação.',
    ], 'alerta': 'Mesmo sem histórico, faça também a inspeção física.'},
    {'chave': 'bateria', 'titulo': 'Verificar a saúde da bateria',
     'icone': 'fa-solid fa-battery-three-quarters', 'itens': [
        'Acesse Ajustes > Bateria > Saúde da Bateria e Carregamento.',
        'Verifique a Capacidade Máxima.',
        'Veja também se há mensagem de “Manutenção” ou bateria não reconhecida.',
    ], 'faixas': [
        ('verde', '85% ou mais', 'excelente'),
        ('amarelo', '80% a 84%', 'atenção'),
        ('vermelho', 'Abaixo de 80%', 'considerar troca de bateria/desconto'),
    ]},
    {'chave': 'faceid', 'titulo': 'Testar o Face ID', 'icone': 'fa-solid fa-face-smile', 'itens': [
        'Acesse Ajustes > Face ID e Código.',
        'Peça ao cliente para desbloquear o aparelho com o rosto.',
        'Teste mais de uma vez.',
        'Se não reconhecer, apresentar mensagem de indisponível ou não permitir configurar, marque como falha.',
    ]},
    {'chave': 'cameras', 'titulo': 'Testar as câmeras', 'icone': 'fa-solid fa-camera', 'itens': [
        'Abra a câmera e teste a frontal e a traseira.',
        'Alterne entre todas as lentes disponíveis (0,5x / 1x / 2x / 3x / 5x).',
        'Tire uma foto e grave um vídeo.',
        'Teste o foco em objetos próximos e distantes.',
        'Verifique se há manchas, imagem embaçada, câmera preta ou tremedeira anormal.',
    ]},
    {'chave': 'tela', 'titulo': 'Testar a tela', 'icone': 'fa-solid fa-mobile-screen-button', 'itens': [
        'Verifique o brilho, o toque e a fluidez.',
        'Abra um fundo branco e um fundo preto (pode ser uma imagem).',
        'Procure por manchas, linhas, pixels mortos, áreas com toque falhando e diferença de cor.',
        'Teste o 3D Touch (em modelos compatíveis) e a sensibilidade geral da tela.',
    ]},
    {'chave': 'audio', 'titulo': 'Testar áudio, microfone e alto-falantes',
     'icone': 'fa-solid fa-volume-high', 'itens': [
        'Faça uma ligação de teste.',
        'Verifique se o som do alto-falante está limpo e alto.',
        'Teste o microfone (grave um áudio).',
        'Teste o áudio de mídia (vídeo ou música).',
        'Verifique se há chiados, falhas ou volume baixo.',
    ]},
    {'chave': 'rede', 'titulo': 'Testar rede celular, Wi-Fi e Bluetooth', 'icone': 'fa-solid fa-signal', 'itens': [
        'Insira um chip e verifique se reconhece e faz chamadas.',
        'Teste o Wi-Fi conectando em uma rede.',
        'Teste o Bluetooth pareando com um acessório (ex.: fone).',
        'Verifique se o sinal está normal e sem quedas.',
    ]},
    {'chave': 'carregamento', 'titulo': 'Testar o carregamento', 'icone': 'fa-solid fa-charging-station', 'itens': [
        'Conecte o carregador e verifique se começa a carregar.',
        'Observe se há folga, mau contato ou se precisa pressionar o cabo.',
        'Confira se o carregamento é normal e sem interrupções.',
    ]},
    {'chave': 'estetico', 'titulo': 'Avaliar o estado estético', 'icone': 'fa-solid fa-magnifying-glass', 'itens': [
        'Analise laterais, traseira e vidro da câmera.',
        'Verifique riscos, amassados, desgaste de bordas e trincos.',
        'Observe se há sinais de queda, oxidação ou peças desalinhadas.',
        'Compare com os padrões da tabela (A/B/C/D) para definir a categoria.',
    ]},
    {'chave': 'restaurar', 'titulo': 'Restaurar o aparelho (após aprovação)',
     'icone': 'fa-solid fa-rotate', 'itens': [
        'Após a negociação, acesse Ajustes > Geral > Transferir ou Redefinir iPhone.',
        'Toque em Apagar Conteúdo e Ajustes.',
        'Confirme que o aparelho inicia na tela “Olá”, sem pedir a conta antiga.',
    ]},
]

# Que passo(s) abrem ao passar o mouse (ou tocar no "?") em cada item do checklist.
# Item sem passo que o explique fica sem a ajuda.
PASSOS_DO_ITEM = {
    # itens obrigatórios
    'capa': ['acessorios'],
    'conta': ['icloud'],
    'fabrica': ['restaurar'],
    'carregador': ['carregamento'],
    # funcionalidades
    'bateria': ['bateria', 'pecas'],
    'rede': ['rede'],
    'wifi': ['rede'],
    'bluetooth': ['rede'],
    'cameras': ['cameras', 'faceid'],
    'microfone': ['audio'],
    'audio': ['audio'],
    # condição estética
    'tela': ['tela', 'pecas'],
    'traseira': ['estetico'],
    'laterais': ['estetico'],
    'cameras_lentes': ['cameras'],
    'botoes': ['estetico'],
    'marcas_uso': ['estetico'],
    'trincos': ['estetico', 'pecas'],
}

# Não há API pública (nem da Receita nem da Anatel) para conferir o IMEI: a consulta
# oficial de aparelho com restrição (roubo, furto, extravio) é a página da ABR Telecom
# indicada pela Anatel, com captcha. A tela leva até ela com o IMEI copiado.
CONSULTA_IMEI_URL = 'https://www.consultaaparelhoimpedido.com.br/public-web/welcome'


def passos_por_chave():
    return {passo['chave']: passo for passo in PASSO_A_PASSO}
