"""Link clicável no corpo do comunicado (/communications/create/ e edit).

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
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

from communications.models import Communication
from communications.views import _limpar_links
from users.models import Sector

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
    print('== BARRA DE FERRAMENTAS ==')
    for arq in ['templates/communications/create.html',
                'templates/communications/edit.html']:
        html = ler(arq)
        curto = arq.split('/')[-1]
        t(f'{curto}: tem o botão de link', 'rcAbrirLink()' in html)
        t(f'{curto}: tem o botão de tirar link', 'rcTirarLink()' in html)
        t(f'{curto}: carrega o editor-link.js', 'js/editor-link.js' in html)
        t(f'{curto}: o clique não rouba a seleção',
          'onmousedown="event.preventDefault()"' in html)
        t(f'{curto}: URL colada sozinha vira link', 'rcLinkDePaste(paste)' in html)

    print('\n== O SCRIPT ==')
    js = ler('static/js/editor-link.js')
    t('não usa window.prompt (WebView sem onJsPrompt devolve null calado)',
      'prompt(' not in js)
    t('bloqueia javascript:, data:, vbscript: e file:',
      "javascript|data|vbscript|file" in js)
    t('externo abre em aba nova', "setAttribute('target', '_blank')" in js)
    t('e com rel seguro', "'noopener noreferrer'" in js)
    t('interno não abre em aba nova', "removeAttribute('target')" in js)
    t('guarda e devolve a seleção', 'selecaoGuardada' in js and 'devolverSelecao' in js)
    t('escapa o texto antes de montar o HTML', 'function escapar' in js)
    t('sincroniza o textarea escondido', 'window.syncContent' in js)

    print('\n== ESTILO ==')
    css = ler('static/css/custom.css')
    t('a caixa de link tem estilo', '#rc-caixa-link' in css)
    t('funciona no tema escuro', 'html.dark #rc-caixa-link' in css)
    t('link parece link no editor', '#message-editor a' in css)
    t('e no comunicado publicado', '.prose a' in css)
    t('as chaves do CSS estão balanceadas', css.count('{') == css.count('}'))

    print('\n== GUARDA DO SERVIDOR (o cliente pode ser burlado) ==')
    casos = [
        ('https://vivo.com.br', True, 'endereço normal'),
        ('/impulso/metas/', True, 'link interno do portal'),
        ('mailto:rh@redeconfianca.com.br', True, 'e-mail'),
        ('tel:+552799999999', True, 'telefone'),
        ('javascript:alert(1)', False, 'javascript:'),
        ('JaVaScRiPt:alert(1)', False, 'javascript: com maiúsculas'),
        ('java\tscript:alert(1)', False, 'javascript: com tab no meio'),
        ('&#106;avascript:alert(1)', False, 'javascript: escapado em entidade'),
        ('data:text/html,x', False, 'data:'),
        ('vbscript:msgbox', False, 'vbscript:'),
    ]
    for href, deve_manter, rotulo in casos:
        saida = _limpar_links(f'<a href="{href}">x</a>')
        t(f'{rotulo} {"passa" if deve_manter else "é neutralizado"}',
          ('href=' in saida) == deve_manter, saida[:70])

    t('href com aspas simples também é limpo',
      'href=' not in _limpar_links("<a href='javascript:x'>i</a>"))
    t('o resto do HTML não é tocado',
      _limpar_links('<b>oi</b> <i>tchau</i>') == '<b>oi</b> <i>tchau</i>')
    t('mensagem vazia não quebra', _limpar_links('') == '' and _limpar_links(None) is None)

    print('\n== DA TELA ATÉ O BANCO ==')
    area = Sector.objects.create(name='ZZ Area Link')
    chefe = User.objects.create_user(
        username='lk.chefe', email='lk.chefe@exemplo-teste.local',
        password='S3nha!teste', sector=area, first_name='Chefe', last_name='Link',
        is_superuser=True, is_staff=True, hierarchy='SUPERADMIN')

    c = Client()
    c.force_login(chefe)
    corpo = ('Acesse o <a href="https://www.vivo.com.br/cursos" target="_blank" '
             'rel="noopener noreferrer">portal da Vivo</a> e anexe o comprovante.')
    r = c.post('/communications/create/', {
        'title': 'ZZ Comunicado com link', 'message': corpo,
        'send_to_all': 'on', 'sender_group': '',
    }, follow=True)
    com = Communication.objects.filter(title='ZZ Comunicado com link').first()
    t('o comunicado foi criado', com is not None, r.status_code)
    t('o link sobreviveu ao salvamento', com and 'href="https://www.vivo.com.br/cursos"' in com.message)
    t('com target', com and 'target="_blank"' in com.message)
    t('e com rel', com and 'noopener noreferrer' in com.message)

    if com:
        html = c.get(f'/communications/{com.id}/').content.decode()
        t('e chega clicável na tela de leitura',
          'href="https://www.vivo.com.br/cursos"' in html)

    r = c.post('/communications/create/', {
        'title': 'ZZ Comunicado perigoso',
        'message': '<a href="javascript:alert(1)">clique</a>',
        'send_to_all': 'on', 'sender_group': '',
    }, follow=True)
    ruim = Communication.objects.filter(title='ZZ Comunicado perigoso').first()
    t('POST forjado com javascript: é criado sem o href', ruim is not None)
    t('e o href sumiu', ruim and 'javascript:' not in ruim.message, ruim.message if ruim else '')

    if ruim:
        ruim_id = ruim.id
        c.post(f'/communications/{ruim_id}/edit/', {
            'title': 'ZZ Comunicado perigoso',
            'message': '<a href="javascript:alert(2)">de novo</a>',
            'send_to_all': 'on', 'sender_group': '',
        }, follow=True)
        ruim.refresh_from_db()
        t('a edição também limpa', 'javascript:' not in ruim.message, ruim.message)

    print('\n== QUEM PODE ==')
    comum = User.objects.create_user(
        username='lk.comum', email='lk.comum@exemplo-teste.local',
        password='S3nha!teste', sector=area, first_name='Comum', last_name='Link')
    cc = Client()
    cc.force_login(comum)
    antes = Communication.objects.count()
    cc.post('/communications/create/', {
        'title': 'ZZ Nao deve existir', 'message': 'x', 'send_to_all': 'on'}, follow=True)
    t('quem não gerencia usuários não cria comunicado',
      Communication.objects.count() == antes)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
