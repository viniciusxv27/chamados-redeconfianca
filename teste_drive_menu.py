"""Drive: liberado no menu — e disponível no Assistente de Apresentações para virar tutorial.

Pedido: "em /drive — liberar no menu; adicionar ao módulo de apresentações para
conseguir criar tutorial".

- o item "Drive" aparece para quem tem o que ver lá: SUPERADMIN e quem é gestor
  ou tem permissão em algum setor mapeado; quem não tem acesso a nada não vê;
- com o módulo desligado na configuração do Drive, só o SUPERADMIN vê o item;
- o catálogo das Apresentações é o próprio menu: com o item, o Drive entra na
  lista de módulos para gerar tutorial, com as telas e o contexto (inclusive os
  níveis de acesso de drive/permissions.py).

Transação desfeita no fim; nenhuma chamada ao Google (só cadastro local).
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.conf import settings
from django.test.utils import setup_test_environment

setup_test_environment()            # o Client guarda o contexto da tela (o catálogo das Apresentações)
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from apresentacoes import modulos
from drive.context_processors import limpar_cache_do_menu
from drive.models import DriveConfig, DrivePermission, SectorDriveMapping
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


ITEM = 'data-rota="/drive/"'
marcador = transaction.atomic()
marcador.__enter__()
try:
    def novo(username, hierarquia='PADRAO', setor=None):
        return User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local',
                                        password='S3nha!teste', first_name=username.split('.')[1].title(),
                                        last_name='Teste', hierarchy=hierarquia, sector=setor)

    setor = Sector.objects.create(name='ZZ Setor Drive Menu')
    outro_setor = Sector.objects.create(name='ZZ Setor Sem Drive')
    mapa = SectorDriveMapping.objects.create(sector=setor, folder_id='zz-pasta-teste', folder_name='ZZ Pasta')
    chefe = novo('zzdm.chefe', 'SUPERADMIN')
    gestor = novo('zzdm.gestor', setor=outro_setor)
    mapa.managers.add(gestor)
    do_setor = novo('zzdm.dosetor', setor=setor)
    DrivePermission.objects.create(mapping=mapa, alvo=DrivePermission.Alvo.SECTOR, target_sector=setor,
                                   nivel=DrivePermission.Nivel.VIEW)
    ninguem = novo('zzdm.ninguem', setor=outro_setor)
    pessoas = [chefe, gestor, do_setor, ninguem]

    def menu_de(u):
        limpar_cache_do_menu([u.pk])
        c = Client()
        c.force_login(u)
        return c.get('/drive/').content.decode()

    print('== O ITEM NO MENU ==')
    html = {u.username: menu_de(u) for u in pessoas}
    t('o SUPERADMIN vê o Drive no menu', ITEM in html['zzdm.chefe'])
    t('o gestor de um setor mapeado também', ITEM in html['zzdm.gestor'])
    t('quem tem permissão pelo setor também', ITEM in html['zzdm.dosetor'])
    t('quem não tem acesso a nenhum setor do Drive não vê o item', ITEM not in html['zzdm.ninguem'])
    t('o item leva só para o Drive (nada de "Meu Drive" para quem não é SUPERADMIN)',
      '/drive/meu-drive/' not in html['zzdm.dosetor'] and 'Meu Drive' not in html['zzdm.dosetor'])

    config = DriveConfig.get()
    config.ativo = False
    config.save()
    html = {u.username: menu_de(u) for u in pessoas}
    t('com o módulo desligado na configuração, só o SUPERADMIN vê o item',
      ITEM in html['zzdm.chefe'] and ITEM not in html['zzdm.gestor'] and ITEM not in html['zzdm.dosetor'])
    config.ativo = True
    config.save()

    print('\n== NO ASSISTENTE DE APRESENTAÇÕES ==')
    c = Client()
    c.force_login(chefe)
    r = c.get('/apresentacoes/modulos/?atualizar=1')
    catalogo = {m['label']: m for m in r.context['modulos']} if r.context else {}
    drive = catalogo.get('drive')
    t('o Drive está na lista de módulos para gerar tutorial', r.status_code == 200 and drive is not None,
      sorted(catalogo))
    t('com o nome e o ícone do menu', drive and drive['nome'] == 'Drive' and drive['icone'] == 'fa-hard-drive', drive)
    paginas = modulos.paginas_para_capturar('drive', drive['telas'] if drive else [])
    t('as telas a capturar começam pela entrada do Drive', paginas and paginas[0]['url'] == '/drive/', paginas)
    contexto = modulos.contexto_do_modulo('drive', 'Drive')
    t('o contexto do tutorial explica os níveis de acesso (drive/permissions.py)',
      '[permissions.py]' in contexto and 'VISUALIZAR < DOWNLOAD' in contexto)
    limpar_cache_do_menu([u.pk for u in pessoas])
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
