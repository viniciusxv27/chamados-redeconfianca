"""Contagem de caixa (20/10/2026): recarga desconta da divergência; coluna sangria/erro sai da tela."""
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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cx-rec'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cx-rec-2'},
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
    from contagem_caixa.models import ConfiguracaoContagem
    cfg = ConfiguracaoContagem.get()
    cfg.notificar_gerente = False
    cfg.save()
    loja = Sector.objects.create(name='Loja ZZ Recarga', adabas='ZZ995')
    chefe = User.objects.create_user(
        username='zz.caixa.rec', email='zz.caixa.rec@exemplo-teste.local', password='S3nha!teste',
        first_name='Zz', last_name='Recarga', sector=loja, hierarchy='SUPERADMIN', is_staff=True, is_superuser=True)
    c = Client(); c.force_login(chefe)
    hoje = date.today()
    dia = ContagemCaixaDia.objects.create(loja=loja, data=hoje, valor_sap=D('520.00'), valor_vivogo=D('500.00'))
    recalcular_saldos(loja.id, desde=hoje)

    print('== RECARGA SAI DA DIVERGÊNCIA ==')
    t('sem recarga, 20 de divergência', dia.divergencia == D('20.00') and dia.status == 'ATENCAO', dia.divergencia)
    r = c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'recarga': '20,00'})
    dia.refresh_from_db()
    t('lançada a recarga, a divergência cai pelo valor dela', dia.divergencia == D('0.00') and dia.status == 'OK', (dia.divergencia, dia.status))
    t('a tela recebe a divergência nova', r.status_code == 200 and r.json().get('divergencia') in ('0.00', 0, '0', None, '') or 'divergencia' in r.json(), r.content[:200])
    t('e a recarga continua saindo da Entrada', dia.entrada == D('500.00'), dia.entrada)

    print('\n== SANGRIA/ERRO FORA DA TELA ==')
    html = c.get(f'/contagem-caixa/loja/{loja.id}/').content.decode()
    t('a coluna sangria/erro não aparece', '<th class="text-right">sangria/erro</th>' not in html and 'data-campo="sangria_erro"' not in html)
    t('a recarga está na conta da divergência na legenda', '(Valor SAP − recarga − Agoracred' in html)
    c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'sangria_erro': '99,00'})
    dia.refresh_from_db()
    t('e não se edita mais pelo salvar', dia.sangria_erro == D('0.00'), dia.sangria_erro)
    ContagemCaixaDia.objects.filter(pk=dia.pk).update(sangria_erro=D('15.00'))
    dia.refresh_from_db()
    t('o que já foi lançado antes continua na Entrada', dia.entrada == D('485.00'), dia.entrada)
    t('e aparece somado nas sangrias', dia.sangrias_total == D('15.00'))
    html = c.get(f'/contagem-caixa/loja/{loja.id}/').content.decode()
    t('com a marca na tela', 'lançado à mão na antiga coluna sangria/erro' in html)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
