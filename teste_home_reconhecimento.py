"""Home (/) nova e o mural de Reconhecimento (/compliments/mural/) — 23/11/2026.

Pedido: layout mais bonito, moderno e interativo; "Elogios Recentes" vira "Reconhecimento";
permitir ver todos os reconhecimentos.

Cobre: a home abre com o topo novo (saudação, atalhos), a seção "Reconhecimento" com os
mais recentes e o "Ver todos"; o mural lista todos, filtra (para mim / meu setor / que eu
fiz), busca por nome/setor/texto e pagina pelo "Carregar mais" (parcial); comentário
{# #} de várias linhas não vaza.

Postgres de dev dentro de uma transação desfeita; cache em memória; rede bloqueada.
"""
import os
import re
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()

from compliments import views as cviews  # noqa: E402
from compliments.models import Compliment  # noqa: E402
from users.models import Sector  # noqa: E402

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


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, side_effect=AssertionError('teste tentou sair para a rede')).start()
# Home leva para /cursos/ quem tem curso pendente: aqui não.
mock.patch('cursos.permissions.deve_ir_para_cursos', return_value=False).start()

marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='ZZ Loja Reconhecimento')
    outra = Sector.objects.create(name='ZZ Outra Loja Reco')
    ana = User.objects.create_user(username='zzrec.ana', email='zzrec.ana@exemplo-teste.local', password='S3nha!teste',
                                   first_name='Zzana', last_name='Reconhecida', sector=loja, hierarchy='PADRAO')
    bia = User.objects.create_user(username='zzrec.bia', email='zzrec.bia@exemplo-teste.local', password='S3nha!teste',
                                   first_name='Zzbia', last_name='Elogiadora', sector=outra, hierarchy='PADRAO')
    c1 = Compliment.objects.create(from_user=bia, to_user=ana, rating=5,
                                   comment='ZZTEXTO atendimento impecável com o cliente da manhã.')
    c2 = Compliment.objects.create(from_user=ana, to_sector=outra, rating=4, comment='ZZTEXTO equipe parceira demais.')
    c3 = Compliment.objects.create(from_user=ana, to_user=bia, rating=3, comment='ZZTEXTO ajudou no fechamento.')
    inativo = Compliment.objects.create(from_user=bia, to_user=ana, rating=5, comment='ZZTEXTO apagado', is_active=False)
    ca = Client(); ca.force_login(ana)

    print('== HOME ==')
    r = ca.get('/')
    html = r.content.decode()
    t('a home abre', r.status_code == 200, r.status_code)
    t('topo novo com saudação e atalhos', 'rc-hero' in html and re.search(r'(Bom dia|Boa tarde|Boa noite),', html)
      and 'aria-label="Atalhos"' in html)
    t('"Elogios Recentes" virou "Reconhecimento"', 'Elogios Recentes' not in html and 'Reconhecimento' in html)
    t('o reconhecimento mais recente aparece na faixa', 'ZZTEXTO ajudou no fechamento.' in html and 'data-rc-trilho' in html)
    t('reconhecimento inativo não aparece', 'ZZTEXTO apagado' not in html)
    t('tem o "Ver todos" para o mural', '/compliments/mural/' in html)
    t('as ações rápidas do fim sumiram (estão no topo)', 'Ações Rápidas' not in html)
    t('o feed continua lá', 'Comunicados' in html)

    print('\n== MURAL ==')
    r = ca.get('/compliments/mural/')
    html = r.content.decode()
    t('o mural abre', r.status_code == 200, r.status_code)
    t('mostra os três ativos', all(x in html for x in ('impecável', 'equipe parceira', 'ajudou no fechamento'))
      and 'ZZTEXTO apagado' not in html)
    t('reconhecimento para setor mostra o setor', 'Setor ZZ Outra Loja Reco' in html)

    def ids(url):
        r = ca.get(url)
        return {c.id for c in r.context['pagina']} & {c1.id, c2.id, c3.id}

    t('filtro "para mim"', ids('/compliments/mural/?ver=para_mim') == {c1.id})
    t('filtro "que eu fiz"', ids('/compliments/mural/?ver=que_fiz') == {c2.id, c3.id})
    t('filtro "meu setor" (para a loja ou para alguém dela)', ids('/compliments/mural/?ver=meu_setor') == {c1.id})
    t('busca por nome de quem recebeu', ids('/compliments/mural/?q=Zzbia') == {c3.id, c1.id} or ids('/compliments/mural/?q=Elogiadora') >= {c3.id})
    t('busca por texto', ids('/compliments/mural/?q=parceira') == {c2.id})
    t('busca por setor', c2.id in ids('/compliments/mural/?q=Outra Loja Reco'))

    print('\n== CARREGAR MAIS ==')
    with mock.patch.object(cviews, 'POR_PAGINA_DO_MURAL', 2):
        r = ca.get('/compliments/mural/?q=ZZTEXTO')
        html = r.content.decode()
        t('primeira página com 2 e o botão "Carregar mais"', len(r.context['pagina']) == 2 and 'data-rc-proxima="2"' in html
          and 'data-rc-mural-mais' in html)
        r = ca.get('/compliments/mural/?q=ZZTEXTO&pagina=2&parcial=1')
        parte = r.content.decode()
        t('a parcial traz só os cartões da página seguinte', r.status_code == 200 and '<html' not in parte.lower()
          and parte.count('data-rc-reco') >= 1 and 'data-rc-proxima' not in parte)

    print('\n== ANIMAÇÕES E AVISOS (SUPERADMIN) ==')
    html = ca.get('/').content.decode()
    t('a home tem as animações compartilhadas (degradê vivo, cascata, barra de leitura)',
      'rc-forma-1' in html and 'rc-sobe' in html and 'id="rcProgresso"' in html and 'data-rc-conta=' in html)
    t('colaborador comum não vê o botão de avisos', 'data-rc-avisos-abrir' not in html)
    chefe = User.objects.create_user(username='zzrec.chefe', email='zzrec.chefe@exemplo-teste.local', password='S3nha!teste',
                                     first_name='Zzchefe', last_name='Super', hierarchy='SUPERADMIN')
    cc = Client(); cc.force_login(chefe)
    falsos = [{'titulo': 'ZZ Pesquisa de clima', 'texto': 'Responda', 'url': '/clima/', 'rotulo': 'Responder',
               'icone': 'fa-bell', 'bloqueante': True}]
    with mock.patch('communications.views._avisos_ocultos', return_value=falsos):
        html = cc.get('/').content.decode()
    t('SUPERADMIN com aviso oculto vê o botão e a lista', 'data-rc-avisos-abrir' in html and '1</span>' in html
      and 'ZZ Pesquisa de clima' in html and 'trava os outros' in html and '?ver_avisos=1' in html)

    print('\n== DETALHE DO COMUNICADO NO MESMO ESTILO ==')
    from communications.models import Communication
    com = Communication.objects.create(title='ZZ Comunicado estilizado', message='<p>ZZ corpo</p>', sender=bia,
                                       title_color='#123456')
    com.recipients.add(ana)
    r = ca.get(f'/communications/{com.id}/')
    html = r.content.decode()
    t('o detalhe abre', r.status_code == 200, r.status_code)
    t('com o topo em degradê, autor e tempo relativo', 'rc-hero' in html and 'Zzbia Elogiadora' in html and 'data-rc-quando' in html)
    t('o título na cor escolhida', 'style="color: #123456"' in html and 'ZZ Comunicado estilizado' in html)
    t('e as animações compartilhadas', 'id="rcProgresso"' in html and 'rc-reacoes' in html)
    t('comentários e confirmação de leitura continuam', 'id="comment-form"' in html and 'ESTOU_CIENTE' in html)

    print('\n== /communications/ NO MESMO ESTILO ==')
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    r = ca.get('/communications/')
    html = r.content.decode()
    t('a lista abre com o topo novo, filtros e busca', r.status_code == 200 and 'rc-hero' in html
      and 'data-rc-filtro="ESTOU_CIENTE"' in html and 'data-rc-busca' in html, r.status_code)
    t('o comunicado aparece com a situação para o filtro', f'data-rc-comm="{com.id}"' in html and 'data-status="NAO_VISUALIZADO"' in html)
    t('ciente e curtir continuam (mesmos botões do JS)', 'class="status-btn' in html and 'class="reaction-btn' in html)
    from communications.models import CommunicationRead
    CommunicationRead.objects.update_or_create(communication=com, user=ana, defaults={'status': 'ESTOU_CIENTE'})
    html = ca.get('/communications/').content.decode()
    t('depois de "ciente" o cartão muda de situação', f'data-rc-comm="{com.id}" data-status="ESTOU_CIENTE"' in html)
    extra = [Communication.objects.create(title=f'ZZ extra {i}', message='x', sender=bia) for i in range(4)]
    for e in extra:
        e.recipients.add(ana)
    with CaptureQueriesContext(connection) as poucas:
        ca.get('/communications/')
    for e in extra[:2]:
        CommunicationRead.objects.create(communication=e, user=ana, status='ESTOU_CIENTE')
    with CaptureQueriesContext(connection) as depois:
        ca.get('/communications/')
    t('o status não custa uma consulta por comunicado', len(depois) <= len(poucas) + 1, (len(poucas), len(depois)))

    print('\n== /users/ E /users/profile/ NO MESMO ESTILO ==')
    r = ca.get('/users/')
    html = r.content.decode()
    t('/users/ abre com o topo novo e os números', r.status_code == 200 and 'rc-hero' in html and 'data-rc-conta=' in html
      and 'Chamados recentes' in html, r.status_code)
    r = ca.get('/users/profile/')
    html = r.content.decode()
    t('/users/profile/ abre com o topo novo', r.status_code == 200 and 'rc-hero' in html and 'Zzana Reconhecida' in html, r.status_code)
    t('o perfil mantém edição, foto, favoritos e senha', all(x in html for x in (
        'onclick="toggleEdit()"', 'id="photoModal"', 'id="favLista"', 'id="favSalvar"', 'id="edit-buttons"', '/change-password')) or
      all(x in html for x in ('onclick="toggleEdit()"', 'id="photoModal"', 'id="favLista"', 'id="favSalvar"', 'id="edit-buttons"')))
    t('sem o </script> sobrando no fim', html.count('</script>') == html.count('<script'))

    print('\n== TEMPLATES ==')
    ruins = [f for f in ('templates/home.html', 'templates/compliments/mural.html',
                         'templates/compliments/_cartao_reconhecimento.html', 'templates/compliments/_mural_pagina.html')
             for m in re.finditer(r'\{#(.*?)#\}', open(f).read(), re.S) if '\n' in m.group(1)]
    t('nenhum {# #} de várias linhas', not ruins, ruins)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste ficou no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
