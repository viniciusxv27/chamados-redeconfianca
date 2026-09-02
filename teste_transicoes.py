"""Transição entre telas + popups que cabem no celular.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import re
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def ler(caminho):
    with open(caminho, encoding='utf-8') as f:
        return f.read()


marcador = transaction.atomic()
marcador.__enter__()
try:
    css = ler('static/css/transicoes.css')
    js = ler('static/js/transicoes.js')
    base = ler('templates/base.html')
    home = ler('templates/home.html')

    print('== O NAVEGADOR NOVO FAZ A TRANSIÇÃO SOZINHO ==')
    t('a regra de navegação existe', '@view-transition' in css and 'navigation: auto' in css)
    t('o conteúdo entra subindo', '::view-transition-new(root)' in css and 'rc-sobe' in css)
    t('o conteúdo antigo sai', '::view-transition-old(root)' in css and 'rc-some' in css)
    t('o menu é capturado à parte', re.search(r'#sidebar\s*\{ view-transition-name: rc-menu', css))
    t('a barra do topo também', re.search(r'#rc-topo\s*\{ view-transition-name: rc-topo', css))
    t('menu e topo não fazem cross-fade (o item ativo muda de tela para tela)',
      '::view-transition-old(rc-menu)' in css and 'animation: none' in css)

    print('\n== O NAVEGADOR ANTIGO NÃO ANIMA O CONTEÚDO ==')
    # Aqui havia uma animação de entrada no #rc-conteudo, e foi ela que travou
    # o app: animação parada no quadro 0 segura opacity: 0 para sempre, e a
    # linha do tempo não anda enquanto o documento não é pintado (WebView
    # abrindo em segundo plano). A tela ficava invisível com os overlays ainda
    # capturando o toque. Estas asserções existem para isso não voltar.
    sem_comentarios = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    t('nenhuma regra encosta no #rc-conteudo', '#rc-conteudo' not in sem_comentarios,
      [l for l in sem_comentarios.splitlines() if '#rc-conteudo' in l])
    t('nenhuma animação de opacidade no conteúdo real',
      'rc-aparece' not in sem_comentarios)
    t('o JS não marca mais plano B nenhum', 'rc-fallback' not in js)
    t('e não esmaece o conteúdo', 'rc-saindo' not in js and 'rc-saindo' not in css)
    t('quem tem navegador antigo ainda ganha a barrinha', '#rc-barra' in sem_comentarios)

    print('\n== NADA PODE DEIXAR A TELA APAGADA NEM PRESA ==')
    t('volta por tempo se a navegação não acontecer', 'setTimeout(desfazer, 1400)' in js)
    t('volta ao voltar pelo histórico', "addEventListener('pageshow', desfazer)" in js)
    t('volta ao reabrir o app', "visibilityState === 'visible'" in js)
    t('a barrinha nunca captura toque', 'pointer-events: none;' in css)
    t('quem pede menos movimento não recebe nenhum',
      'prefers-reduced-motion: reduce' in css)

    print('\n== A BARRINHA SÓ APARECE EM LINK QUE TROCA DE TELA ==')
    for trecho, porque in [
        ("a.hasAttribute('download')", 'link de download'),
        ("a.target && a.target !== '_self'", 'abre em outra aba'),
        ("url.origin !== location.origin", 'site de fora'),
        ("href.charAt(0) === '#'", 'âncora'),
        ("javascript|mailto|tel", 'javascript:/mailto:/tel:'),
        ("download|export|exportar|baixar", 'rota que baixa arquivo'),
        ("^\\/media\\/", 'arquivo em /media/'),
        ("e.metaKey || e.ctrlKey", 'clique com ctrl/cmd'),
    ]:
        t(f'não dispara em {porque}', trecho in js)
    t('nunca chega a 100%: quem fecha é a tela nova', '100% { width: 94%; }' in css)

    print('\n== NÃO ANIMA O QUE FOI CANCELADO ==')
    # onsubmit="return confirm(...)" é o padrão do portal: escutando na captura,
    # a tela esmaecia enquanto o diálogo estava aberto.
    t('escuta na subida, não na captura',
      "comecar();\n    });" in js and "comecar();\n    }, true);" not in js)
    t('respeita quem cancelou', 'if (e.defaultPrevented' in js)

    print('\n== LIGADO NO BASE.HTML ==')
    t('o CSS entra', "css/transicoes.css" in base)
    t('o JS entra sem travar o carregamento', "js/transicoes.js' %}\" defer" in base)
    t('o topo tem id', 'id="rc-topo"' in base)
    t('o conteúdo tem id', 'id="rc-conteudo"' in base)
    t('a tela de login também tem', base.count('id="rc-conteudo"') == 2, base.count('id="rc-conteudo"'))

    print('\n== POPUP DA JANELA DE EXPERIÊNCIA CABE NA TELA ==')
    t('o cartão é limitado pela altura visível',
      'max-h-[calc(100dvh-1.5rem)]' in home)
    t('cabeçalho, lista e rodapé em coluna', 'experience-window-card' in home and 'flex flex-col' in home)
    t('a lista rola, o resto fica parado', 'flex-1 min-h-0 overflow-y-auto' in home)
    t('nada mais usa altura fixa de viewport', not re.search(r'max-h-\[\d+vh\]', home))
    t('o cabeçalho quebra em vez de empurrar o X', 'justify-between gap-2 flex-wrap' in home)
    t('no celular o botão Exportar vira só o ícone',
      '<span class="hidden sm:inline">Exportar</span>' in home)
    t('o mesmo vale para as trilhas obrigatórias',
      home.count('max-h-[calc(100dvh-1.5rem)]') == 2, home.count('max-h-[calc(100dvh-1.5rem)]'))

    print('\n== O RESTO DOS MODAIS ==')
    # vh no celular ignora a barra do app e corta o rodapé do modal
    import pathlib
    sobrou = []
    for f in pathlib.Path('templates').rglob('*.html'):
        linhas = ler(f).splitlines()
        for i, l in enumerate(linhas):
            if re.search(r'max-h-\[\d+vh\]', l) and any(
                    'fixed inset-0' in linhas[j] for j in range(max(0, i - 4), i + 1)):
                sobrou.append(f'{f}:{i+1}')
    t('nenhum cartão de modal ficou em vh', not sobrou, sobrou[:3])

    ativ = ler('templates/projects/activity_modal.html')
    t('o modal de atividade não corta mais o fim',
      'flex flex-col' in ativ and 'overflow-y-auto flex-1 min-h-0' in ativ)
    t('a bolha do chat fica atrás dos modais',
      'id="supportChatWidget" class="fixed bottom-4 right-4 z-40"' in base)

    print('\n== AS TELAS AINDA ABREM ==')
    u = User.objects.filter(is_superuser=True, is_active=True).first()
    c = Client()
    c.force_login(u)
    for nome, url in [('home', '/'), ('metas', '/impulso/metas/'), ('chamados', '/tickets/'),
                      ('escala', '/ponto/escala/'), ('reuniões', '/reunioes/')]:
        r = c.get(url, follow=True)
        t(f'{nome} responde 200', r.status_code == 200, r.status_code)
        if r.status_code == 200:
            corpo = r.content.decode()
            t(f'{nome} carrega a transição', 'css/transicoes.css' in corpo)

    print('\n== O FILTRO DO KANBAN NÃO EMPURRA A PÁGINA ==')
    kb = ler('templates/impulso/metas_kanban.html')
    t('o select cabe na largura do celular',
      'w-full sm:w-auto max-w-full text-sm border border-gray-300' in kb)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
