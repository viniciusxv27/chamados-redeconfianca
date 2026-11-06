"""Contagem de caixa (06/11/2026): ALLIED em quantidade de vendas Renova, divergência com a
recarga somada ao SAP e uma categoria de depósito só nas sangrias.

Roda numa transação desfeita no fim (as migrações novas do caixa são aplicadas dentro dela).
"""
import os
import sys
from datetime import date, timedelta
from decimal import Decimal as D

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.cache import caches
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


marcador = transaction.atomic()
marcador.__enter__()
try:
    ex = MigrationExecutor(connection)
    folhas = ex.loader.graph.leaf_nodes('contagem_caixa')
    if ex.migration_plan(folhas):
        ex.migrate(folhas)

    from contagem_caixa.models import CategoriaSangria, ConfiguracaoContagem, ContagemCaixaDia
    from contagem_caixa.servicos import divergencias_para_avisar, recalcular_saldos
    from contagem_caixa.servicos_texto import chave_nome
    from users.models import Sector

    cfg = ConfiguracaoContagem.get()
    cfg.notificar_gerente = False
    cfg.save()
    loja = Sector.objects.create(name='Loja ZZ Allied', adabas='ZZ996')
    chefe = User.objects.create_user(
        username='zz.caixa.all', email='zz.caixa.all@exemplo-teste.local', password='S3nha!teste',
        first_name='Zz', last_name='Allied', sector=loja, hierarchy='SUPERADMIN', is_staff=True, is_superuser=True)
    c = Client()
    c.force_login(chefe)
    hoje = date.today()

    print('== DIVERGÊNCIA = (SAP + recarga − Agoracred − Renova − Transferências) − Vivo go ==')
    dia = ContagemCaixaDia.objects.create(loja=loja, data=hoje, valor_sap=D('1000.00'), recarga=D('50.00'),
                                          agoracred=D('100.00'), renova=D('200.00'), transferencias=D('30.00'),
                                          valor_vivogo=D('720.00'))
    t('a fórmula nova fecha (1000 + 50 − 100 − 200 − 30 = 720)', dia.sap_comparavel == D('720.00')
      and dia.divergencia == D('0.00') and dia.status == 'OK', (dia.sap_comparavel, dia.divergencia))
    dia.valor_vivogo = D('620.00')
    dia.save()
    t('quando não bate, a diferença aparece', dia.divergencia == D('100.00') and dia.status == 'ATENCAO', dia.divergencia)

    # As listas do banco (aviso ao gerente e painel) usam a mesma conta.
    ontem = hoje - timedelta(days=1)
    ContagemCaixaDia.objects.create(loja=loja, data=ontem, valor_sap=D('500.00'), recarga=D('40.00'),
                                    valor_vivogo=D('540.00'))
    gerente = User.objects.create_user(
        username='zz.caixa.ger', email='zz.caixa.ger@exemplo-teste.local', password='S3nha!teste',
        first_name='Zz', last_name='Gerente', sector=loja, job_title='GERENTE DE VENDAS')
    caches['local'].delete(f'caixa:divergencias:{gerente.pk}')
    avisos = divergencias_para_avisar(gerente)
    datas = [a['data'] for a in avisos]
    t('o aviso de divergência usa a conta nova (dia com recarga que fecha não é avisado)',
      f'{ontem:%d/%m/%Y}' not in datas and f'{hoje:%d/%m/%Y}' in datas, datas)
    html = c.get('/contagem-caixa/').content.decode()
    t('o painel abre com a conta nova', '<html' in html.lower())

    print('\n== ALLIED EM QUANTIDADE ==')
    r = c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'allied_qtd': '3'})
    dia.refresh_from_db()
    t('grava a quantidade de vendas Renova', r.status_code == 200 and dia.allied_qtd == 3, (r.status_code, dia.allied_qtd))
    t('quantidade não mexe em Entrada nem divergência', dia.entrada == D('1000.00') - D('50.00') - D('100.00')
      - D('200.00') - D('30.00') and dia.divergencia == D('100.00'), (dia.entrada, dia.divergencia))
    r = c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'allied_qtd': '2,5'})
    t('quantidade quebrada é recusada', r.status_code == 400 and r.json().get('campo') == 'allied_qtd')
    r = c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'allied_qtd': ''})
    dia.refresh_from_db()
    t('vazio vira zero', dia.allied_qtd == 0)
    c.post(f'/contagem-caixa/loja/{loja.id}/salvar/', {'data': hoje.isoformat(), 'allied': '999,00'})
    dia.refresh_from_db()
    t('o valor em R$ antigo não se edita mais pela tela', dia.allied == D('0.00'))
    antigo = ContagemCaixaDia.objects.create(loja=loja, data=hoje - timedelta(days=40), valor_sap=D('300.00'),
                                             allied=D('25.00'))
    t('dia antigo com allied em R$ continua com ele na Entrada', antigo.entrada == D('275.00'), antigo.entrada)
    html = c.get(f'/contagem-caixa/loja/{loja.id}/').content.decode()
    t('a coluna ALLIED é quantidade', 'data-campo="allied_qtd"' in html and 'data-campo="allied"' not in html
      and 'ALLIED <span' in html)
    t('a legenda traz a divergência nova', '(Valor SAP + recarga − Agoracred − Renova − Transferências) − Vivo go EA' in html)

    print('\n== UMA CATEGORIA DE DEPÓSITO ==')
    depositos = [x for x in CategoriaSangria.objects.all() if chave_nome(x.nome) == 'deposito']
    t('fica uma só "Depósito", que vai para a coluna Depósito',
      len(depositos) == 1 and depositos[0].nome == 'Depósito' and depositos[0].vai_para_deposito,
      [(x.nome, x.vai_para_deposito) for x in depositos])
    antes = CategoriaSangria.objects.count()
    c.post('/contagem-caixa/sangrias/categorias/', {'nova': 'DEPÓSITO'})
    c.post('/contagem-caixa/sangrias/categorias/', {'nova': '  deposito '})
    t('não deixa criar outra com maiúscula/acento diferente', CategoriaSangria.objects.count() == antes)
    c.post('/contagem-caixa/sangrias/categorias/', {'nova': 'ZZ Categoria Nova'})
    t('categoria nova de verdade continua sendo criada', CategoriaSangria.objects.filter(nome='ZZ Categoria Nova').exists())
    outra = CategoriaSangria.objects.get(nome='ZZ Categoria Nova')
    c.post('/contagem-caixa/sangrias/categorias/', {f'nome_{outra.id}': 'depósito', f'ativa_{outra.id}': 'on'})
    t('nem renomear outra para "depósito"', CategoriaSangria.objects.get(pk=outra.pk).nome == 'ZZ Categoria Nova')
    html = c.get('/contagem-caixa/sangrias/').content.decode()
    import re
    sel = re.search(r'<select[^>]*name="categoria"[^>]*>(.*?)</select>', html, re.S)
    opcoes = [chave_nome(o) for o in re.findall(r'<option[^>]*>([^<]*)</option>', sel.group(1))] if sel else []
    t('ao registrar a sangria, "Depósito" aparece uma vez só', opcoes.count('deposito') == 1, opcoes)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
