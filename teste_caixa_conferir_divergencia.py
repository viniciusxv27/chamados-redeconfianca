"""Contagem de caixa: dar OK num dia em Atenção cuja diferença já foi verificada.

Pedido: "a opção de aparecer status de atenção pra dias que estão com
divergência, queria poder dar OK mesmo se tiver divergência... sinalizar que
aquela diferença de caixa já foi verificada e que provavelmente é um ajuste manual".

Cobre: o status OK · conferido, o OK que cai quando a divergência muda, desfazer,
só gestão confere, o dashboard tirando o dia do alerta e a planilha com o botão.

Caches em memória, transação desfeita no fim: não grava nada no banco.
"""
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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cx-conf'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cx-conf-2'},
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

    loja = Sector.objects.create(name='Loja ZZ Conferida', adabas='ZZ996')
    chefe = User.objects.create_user(
        username='zz.caixa.conf', email='zz.caixa.conf@exemplo-teste.local',
        password='S3nha!teste', first_name='Zz', last_name='Conferente',
        sector=loja, hierarchy='SUPERADMIN', is_staff=True, is_superuser=True)
    vendedor = User.objects.create_user(
        username='zz.caixa.vend', email='zz.caixa.vend@exemplo-teste.local',
        password='S3nha!teste', first_name='Zz', last_name='Vendedor',
        sector=loja, hierarchy='PADRAO')
    hoje = date.today()
    dia = ContagemCaixaDia.objects.create(loja=loja, data=hoje, valor_sap=D('500.00'),
                                          valor_vivogo=D('480.00'))
    recalcular_saldos(loja.id, desde=hoje)

    print('== MODELO ==')
    dia.refresh_from_db()
    t('dia divergente começa em Atenção', dia.status == 'ATENCAO' and dia.em_atencao, dia.status)

    c = Client()
    c.force_login(chefe)
    url = f'/contagem-caixa/loja/{loja.id}/conferir/'
    r = c.post(url, {'data': hoje.isoformat(), 'acao': 'conferir', 'nota': 'ajuste manual no SAP'})
    j = r.json()
    t('dar OK responde ok', r.status_code == 200 and j['ok'], r.content[:200])
    t('status vira OK · conferido', j['status'] == 'CONFERIDO' and 'conferido' in j['status_rotulo'], j['status'])
    t('o html do status traz o desfazer', 'data-acao="desfazer"' in j['status_html'])
    dia.refresh_from_db()
    t('guarda quem, quando, a nota e o valor conferido',
      dia.conferido_por_id == chefe.id and dia.conferido_em and dia.conferido_nota == 'ajuste manual no SAP'
      and dia.divergencia_conferida == D('20.00'), (dia.divergencia_conferida, dia.conferido_nota))
    t('não está mais em atenção', not dia.em_atencao and dia.conferido)
    t('a divergência continua aparecendo', dia.divergencia == D('20.00'))

    print('\n== DASHBOARD E PLANILHA ==')
    r = c.get('/contagem-caixa/?dias=5')
    linha = [l for l in r.context['por_loja'] if l['loja'].id == loja.id][0]
    t('dashboard: a loja sai de "em atenção"', linha['atencao'] == 0 and linha['conferidos'] == 1, linha)
    t('e o dia some das divergências recentes', all(d.pk != dia.pk for d in r.context['ultimos_alertas']))
    r = c.get(f'/contagem-caixa/loja/{loja.id}/?mes={hoje.month}&ano={hoje.year}')
    html = r.content.decode()
    t('planilha mostra o selo conferido', 'cx-conferido' in html and 'ajuste manual no SAP' in html)
    t('cartão conta 0 em atenção e 1 conferido', r.context['total_atencao'] == 0 and r.context['total_conferidos'] == 1)

    print('\n== O OK CAI SE O NÚMERO MUDAR ==')
    r = c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'valor_vivogo': '470,00'})
    j = r.json()
    t('mudou o Vivo go: volta para Atenção', j['status'] == 'ATENCAO' and 'data-acao="conferir"' in j['status_html'], j['status'])
    r = c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'valor_vivogo': '480,00'})
    t('voltou ao valor conferido: o OK vale de novo', r.json()['status'] == 'CONFERIDO')
    r = c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'valor_vivogo': '500,00'})
    t('bateu de verdade: OK normal', r.json()['status'] == 'OK')
    c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'valor_vivogo': '480,00'})

    print('\n== DESFAZER E PERMISSÃO ==')
    r = c.post(url, {'data': hoje.isoformat(), 'acao': 'desfazer'})
    t('desfazer volta para Atenção', r.json()['status'] == 'ATENCAO')
    dia.refresh_from_db()
    t('e limpa quem conferiu', dia.divergencia_conferida is None and dia.conferido_por_id is None and dia.conferido_nota == '')
    r = c.post(url, {'data': (hoje.replace(day=1) if hoje.day != 1 else hoje.replace(day=2)).isoformat(), 'acao': 'conferir'})
    t('dia sem lançamento: 404', r.status_code == 404, r.status_code)
    c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'valor_vivogo': '500,00'})
    r = c.post(url, {'data': hoje.isoformat(), 'acao': 'conferir'})
    t('dia sem divergência não leva OK manual', r.status_code == 400, r.status_code)
    c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'valor_vivogo': '480,00'})

    v = Client()
    v.force_login(vendedor)
    r = v.post(url, {'data': hoje.isoformat(), 'acao': 'conferir'})
    dia.refresh_from_db()
    t('quem não é gestão não dá OK', r.status_code in (302, 403) and dia.divergencia_conferida is None, r.status_code)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
