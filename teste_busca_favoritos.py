"""Menu lateral (novos grupos), menus favoritos no perfil e busca global do menu superior.

Roda numa transação desfeita no fim.
"""
import json
import os
import re
import sys
import unicodedata

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
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


def norm(x):
    x = unicodedata.normalize('NFKD', x)
    return ''.join(c for c in x if not unicodedata.combining(c)).lower()


def grupo(html, ident):
    """Rótulos dos itens de um submenu, na ordem da tela."""
    i = html.index(f'id="{ident}"')
    f = html.index('</ul>', i)
    return [r.strip() for r in re.findall(r'<span(?: class="text-sm[^"]*")?>([^<{]+)</span>', html[i:f])]


marcador = transaction.atomic()
marcador.__enter__()
try:
    ex = MigrationExecutor(connection)
    alvos = [n for app in ('users',) for n in ex.loader.graph.leaf_nodes(app)]
    if ex.migration_plan(alvos):
        ex.migrate(alvos)

    from communications.models import Communication
    from facilitador.models import Recurso
    from tickets.models import Ticket
    from users.models import Sector

    S1 = Sector.objects.create(name='ZZ Busca Setor 1')
    S2 = Sector.objects.create(name='ZZ Busca Setor 2')

    def pessoa(username, **kw):
        return User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local', password='S3nha!teste',
                                        first_name='ZZ', last_name=username, **kw)

    chefe = pessoa('zzbf.super', hierarchy='SUPERADMIN', sector=S1)
    comum = pessoa('zzbf.comum', hierarchy='PADRAO', sector=S1)
    outro = pessoa('zzbf.outro', hierarchy='PADRAO', sector=S2, job_title='CONSULTOR XYZBUSCA')
    ca, cc = Client(), Client()
    ca.force_login(chefe)
    cc.force_login(comum)

    print('== MENU LATERAL ==')
    html = ca.get('/users/profile/').content.decode()
    topo = html[html.index('id="sidebar"'):html.index('Área do Colaborador')]
    rotulos_topo = re.findall(r'<span class="text-sm sidebar-text">([^<]+)</span>', topo)
    t('no topo ficam só Home e Power BI (e o bloco de favoritos)', rotulos_topo == ['Home', 'Power BI'], rotulos_topo)
    com = grupo(html, 'colaborador-menu')
    t('Comunicados, Elogios e Mercadinho em Gestão Comercial', {'Comunicados', 'Elogios', 'Mercadinho'} <= set(com), com)
    t('Gestão Comercial em ordem alfabética', com == sorted(com, key=norm), com)
    corp = grupo(html, 'financeiro-menu')
    t('E-mail e Assistente Claude em Gestão Corporativa', {'E-mail', 'Assistente Claude'} <= set(corp), corp)
    t('Gestão Corporativa em ordem alfabética', corp == sorted(corp, key=norm), corp)
    corp_comum = grupo(cc.get('/users/profile/').content.decode(), 'financeiro-menu')
    t('Assistente Claude continua só para SUPERADMIN', 'Assistente Claude' not in corp_comum and 'E-mail' in corp_comum, corp_comum)

    print('\n== FAVORITOS ==')
    t('o perfil tem a seção "Menu favorito"', 'Menu favorito' in html and 'id="favCatalogo"' in html)
    t('o menu tem o bloco de favoritos', 'id="rc-favoritos"' in html and 'id="rc-favoritos-dados"' in html)

    def salvar(corpo, cliente=ca):
        return cliente.post('/users/profile/favoritos/', json.dumps(corpo), content_type='application/json')
    r = salvar({'favoritos': ['/facilitador/', '/quadro-hc/', '/facilitador/', '//malicioso.com', 'http://x.com', 42, '/' + 'a' * 300]})
    chefe.refresh_from_db()
    t('salva só endereços internos, sem repetir, na ordem', r.status_code == 200 and chefe.menu_favoritos == ['/facilitador/', '/quadro-hc/'],
      chefe.menu_favoritos)
    html = ca.get('/users/profile/').content.decode()
    m = re.search(r'<script id="rc-favoritos-dados" type="application/json">(.*?)</script>', html)
    t('o menu recebe a lista (para montar os favoritos)', m and json.loads(m.group(1)) == ['/facilitador/', '/quadro-hc/'])
    t('reordenar troca a ordem', salvar({'favoritos': ['/quadro-hc/', '/facilitador/']}).status_code == 200
      and User.objects.get(pk=chefe.pk).menu_favoritos == ['/quadro-hc/', '/facilitador/'])
    t('lista inválida é recusada', salvar({'favoritos': 'x'}).status_code == 400)
    t('só por POST', ca.get('/users/profile/favoritos/').status_code == 405)
    salvar({'favoritos': ['/quadro-hc/']}, cc)
    t('cada pessoa tem os seus', User.objects.get(pk=comum.pk).menu_favoritos == ['/quadro-hc/']
      and User.objects.get(pk=chefe.pk).menu_favoritos == ['/quadro-hc/', '/facilitador/'])

    print('\n== BUSCA GLOBAL ==')
    t('o menu superior abre a busca', 'data-rc-busca' in html and 'id="rc-busca"' in html and 'Ctrl K' in html)
    meu = Ticket.objects.create(title='ZZ impressora xyzbusca quebrada', description='x', sector=S1, created_by=comum)
    alheio = Ticket.objects.create(title='ZZ servidor xyzbusca caiu', description='x', sector=S2, created_by=outro)
    Communication.objects.create(title='ZZ Comunicado xyzbusca geral', message='oi', sender=chefe, send_to_all=True)
    Recurso.objects.create(titulo='ZZ Manual xyzbusca', tipo='LINK', url='https://exemplo.com', ativo=True)
    Recurso.objects.create(titulo='ZZ Oculto xyzbusca', tipo='LINK', url='https://exemplo.com', ativo=False)

    def busca(q, cliente):
        d = cliente.get('/busca-global/', {'q': q}).json()
        return {g['titulo']: [i['titulo'] for i in g['itens']] for g in d['grupos']}
    rc = busca('xyzbusca', cc)
    t('acha os chamados que a pessoa vê', any('impressora' in x for x in rc.get('Chamados', [])), rc)
    t('e não os dos outros setores', not any('servidor' in x for x in rc.get('Chamados', [])), rc.get('Chamados'))
    t('acha comunicados e o Facilitador visível', rc.get('Comunicados') and rc.get('Facilitador') == ['ZZ Manual xyzbusca'], rc)
    t('pessoas só para quem gerencia usuários', 'Pessoas' not in rc)
    ra = busca('xyzbusca', ca)
    t('quem vê tudo acha todos os chamados', len([x for x in ra.get('Chamados', []) if 'xyzbusca' in x]) == 2, ra.get('Chamados'))
    t('e as pessoas (pelo cargo)', any('zzbf.outro' in x for x in ra.get('Pessoas', [])), ra.get('Pessoas'))
    t('item oculto do Facilitador só para quem gerencia', 'ZZ Oculto xyzbusca' in ra.get('Facilitador', []))
    t('busca pelo número do chamado', any(x.startswith(f'#{meu.id} ') for x in busca(f'#{meu.id}', cc).get('Chamados', [])))
    t('termo curto não busca', busca('x', cc) == {})
    t('sem login não busca', Client().get('/busca-global/', {'q': 'xyzbusca'}).status_code == 302)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
