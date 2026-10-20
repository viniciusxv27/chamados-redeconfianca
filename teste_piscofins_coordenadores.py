"""Menu (20/10/2026): PIS/Cofins sai para quem está no grupo COORDENADORES (só com liberação individual)."""
import os
import sys
from datetime import date
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-pc-co'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-pc-co-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from contagem_caixa.models import ContagemCaixaDia, SaldoInicialMes
from contagem_caixa.servicos import recalcular_saldos
from users.models import Sector

User = get_user_model()
D = Decimal
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


marcador = transaction.atomic()
marcador.__enter__()
try:
    from communications.models import CommunicationGroup
    from users.models import UserModuleAccess
    grupo = CommunicationGroup.objects.filter(name__iexact='COORDENADORES').first() or CommunicationGroup.objects.create(name='COORDENADORES')

    def gente(nome, hier, no_grupo):
        u = User.objects.create_user(username=f'zz.pc.{nome}', email=f'zz.pc.{nome}@exemplo-teste.local',
                                     password='S3nha!teste', first_name='Zz', last_name=nome, hierarchy=hier)
        if no_grupo:
            u.communication_groups.add(grupo)
        return u

    admin_fora = gente('adm', 'ADMIN', False)
    coord = gente('coord', 'ADMIN', True)
    coord_lib = gente('coordlib', 'ADMIN', True)
    UserModuleAccess.objects.create(user=coord_lib, module_key='caixa.piscofins')
    chefe = gente('chefe', 'SUPERADMIN', True)

    def ve(u):
        c = Client(); c.force_login(u)
        menu = 'href="/contagem-caixa/pis-cofins/"' in c.get('/').content.decode()
        pagina = c.get('/contagem-caixa/pis-cofins/').status_code == 200
        return menu, pagina

    print('== PIS/COFINS E O GRUPO COORDENADORES ==')
    t('ADMIN fora do grupo continua vendo (menu e tela)', ve(admin_fora) == (True, True), ve(admin_fora))
    t('ADMIN no grupo COORDENADORES não vê o menu nem abre a tela', ve(coord) == (False, False), ve(coord))
    t('com a liberação individual, o coordenador vê', ve(coord_lib) == (True, True), ve(coord_lib))
    t('o SUPERADMIN no grupo continua vendo', ve(chefe) == (True, True), ve(chefe))
    c = Client(); c.force_login(coord)
    t('o coordenador continua no caixa das lojas', c.get('/contagem-caixa/').status_code == 200)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
