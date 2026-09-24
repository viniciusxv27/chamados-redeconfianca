"""Contagem de caixa: a diferença entra no saldo do dia.

Pedido: "em /contagem-caixa/loja/{id}/ — a regra é que a diferença deve somar
no saldo; o saldo do dia não está sendo alterado no caso de diferença/entrada,
ajuste essa regra, pois no final sempre deve estar o saldo atualizado".

Era isto: o saldo somava só o Valor real. Como nenhuma loja tinha contado um
dia sequer (4.051 linhas no banco, zero com Valor real), o saldo era R$ 0,00 em
toda parte, mesmo com Entrada e Diferença na linha. Agora o saldo do dia é

    saldo anterior + Valor real + Diferença − Depósito

e Valor real + Diferença é a Entrada, então o saldo anda com o que entrou na
gaveta, contado ou não, e o depósito é o que sai.

O que este teste cobre:

- a conta do dia: sem contagem, contada certinha, com falta, com sobra, com
  depósito, com sangria e com serviço de parceiro;
- a corrente: dias em sequência, mês que começa com abertura fixada, mexer num
  dia do meio refazendo os seguintes, dia sem lançamento segurando o saldo;
- a tela da loja: o saldo da última linha, o cartão do topo, a legenda da regra
  e o que o salvar devolve;
- o invariante que resume o pedido: contar o dia muda a Diferença, não o saldo
  (porque Valor real + Diferença continua sendo a Entrada);
- o dashboard somando o saldo de cada loja.

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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cx-saldo'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cx-saldo-2'},
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
    loja = Sector.objects.create(name='Loja ZZ Saldo Diferenca', adabas='ZZ997')
    chefe = User.objects.create_user(
        username='zz.caixa.chefe', email='zz.caixa.chefe@exemplo-teste.local',
        password='S3nha!teste', first_name='Zz', last_name='Chefe',
        sector=loja, hierarchy='SUPERADMIN', is_staff=True, is_superuser=True)

    print('== A CONTA DO DIA ==')

    def dia(**kw):
        """Um dia solto, só para conferir a conta (não vai para o banco)."""
        return ContagemCaixaDia(loja=loja, data=date(2026, 6, 1), **kw)

    d = dia(valor_sap=D('300.00'))
    t('dia que ninguém contou soma a entrada no saldo',
      d.calcular_saldo(D('100.00')) == D('400.00'), d.calcular_saldo(D('100.00')))
    t('e a diferença é a entrada inteira (nada contado)', d.diferenca == D('300.00'), d.diferenca)

    d = dia(valor_sap=D('300.00'), valor_real=D('300.00'))
    t('dia contado sem diferença soma o mesmo',
      d.calcular_saldo(D('100.00')) == D('400.00'), d.calcular_saldo(D('100.00')))

    d = dia(valor_sap=D('300.00'), valor_real=D('250.00'))
    t('faltando R$ 50 na gaveta, o saldo ainda sobe R$ 300',
      d.calcular_saldo(D('100.00')) == D('400.00'), d.calcular_saldo(D('100.00')))
    t('e a falta aparece na diferença', d.diferenca == D('50.00'), d.diferenca)

    d = dia(valor_sap=D('300.00'), valor_real=D('320.00'))
    t('sobrando R$ 20, idem — a sobra entra pela diferença negativa',
      d.calcular_saldo(D('100.00')) == D('400.00'), d.calcular_saldo(D('100.00')))
    t('e a diferença fica negativa', d.diferenca == D('-20.00'), d.diferenca)

    d = dia(valor_sap=D('300.00'), valor_real=D('300.00'), deposito=D('250.00'))
    t('o depósito sai do saldo (foi para o banco)',
      d.calcular_saldo(D('100.00')) == D('150.00'), d.calcular_saldo(D('100.00')))

    d = dia(valor_sap=D('500.00'), sangria_erro=D('50.00'), transferencias=D('100.00'))
    t('sangria e transferência não entram na gaveta',
      d.calcular_saldo(D('0.00')) == D('350.00'), d.calcular_saldo(D('0.00')))

    d = dia(valor_sap=D('500.00'), allied=D('100.00'), recarga=D('20.00'),
            agoracred=D('30.00'), renova=D('50.00'))
    t('serviço de parceiro é repasse, não dinheiro em caixa',
      d.calcular_saldo(D('0.00')) == D('300.00'), d.calcular_saldo(D('0.00')))

    d = dia(valor_sap=D('100.00'), sangria_erro=D('180.00'))
    t('sangria maior que a venda deixa o saldo negativo, e não parado',
      d.calcular_saldo(D('50.00')) == D('-30.00'), d.calcular_saldo(D('50.00')))

    print('\n== A CORRENTE DE DIAS ==')

    def grava(d, sap, real=D('0.00'), dep=D('0.00')):
        return ContagemCaixaDia.objects.create(loja=loja, data=d, valor_sap=sap,
                                               valor_real=real, deposito=dep)

    grava(date(2026, 6, 10), D('200.00'))
    grava(date(2026, 6, 11), D('150.00'), real=D('100.00'))     # faltaram 50
    grava(date(2026, 6, 12), D('300.00'), real=D('300.00'), dep=D('400.00'))
    recalcular_saldos(loja.id)

    d10, d11, d12 = [ContagemCaixaDia.objects.get(loja=loja, data=date(2026, 6, x))
                     for x in (10, 11, 12)]
    t('primeiro dia: 200', d10.saldo == D('200.00'), d10.saldo)
    t('segundo: 200 + 150 mesmo com a falta', d11.saldo == D('350.00'), d11.saldo)
    t('terceiro: 350 + 300 − 400 depositados', d12.saldo == D('250.00'), d12.saldo)

    print('\n-- contar um dia não mexe no saldo, só na diferença --')
    antes = d11.saldo
    d11.valor_real = D('150.00')          # a loja achou os R$ 50
    d11.save()
    recalcular_saldos(loja.id, desde=date(2026, 6, 11))
    d11.refresh_from_db(); d12.refresh_from_db()
    t('o saldo do dia contado não muda', d11.saldo == antes, (antes, d11.saldo))
    t('a diferença é que zera', d11.diferenca == D('0.00'), d11.diferenca)
    t('e os dias seguintes seguem iguais', d12.saldo == D('250.00'), d12.saldo)

    print('\n-- mexer num dia do meio refaz os seguintes --')
    d11.valor_sap = D('250.00')           # a importação corrigiu o dia
    d11.save()
    recalcular_saldos(loja.id, desde=date(2026, 6, 11))
    d11.refresh_from_db(); d12.refresh_from_db()
    t('o dia do meio sobe 100', d11.saldo == D('450.00'), d11.saldo)
    t('e o último sobe junto', d12.saldo == D('350.00'), d12.saldo)

    print('\n-- o mês seguinte continua de onde o anterior parou --')
    grava(date(2026, 7, 2), D('80.00'))
    recalcular_saldos(loja.id)
    jul = ContagemCaixaDia.objects.get(loja=loja, data=date(2026, 7, 2))
    t('julho começa no fechamento de junho', jul.saldo == D('430.00'), jul.saldo)

    SaldoInicialMes.objects.create(loja=loja, ano=2026, mes=7, valor=D('1000.00'),
                                   motivo='abertura do controle', definido_por=chefe)
    recalcular_saldos(loja.id)
    jul.refresh_from_db(); d12.refresh_from_db()
    t('com a abertura fixada, julho parte de 1000', jul.saldo == D('1080.00'), jul.saldo)
    t('e junho não se mexe', d12.saldo == D('350.00'), d12.saldo)

    print('\n== A TELA DA LOJA ==')
    c = Client()
    c.force_login(chefe)
    r = c.get(f'/contagem-caixa/loja/{loja.id}/?mes=6&ano=2026')
    html = r.content.decode()
    t('a loja abre', r.status_code == 200, r.status_code)

    dias = {d.data: d for d in r.context['dias']}
    t('todo dia do mês aparece', len(r.context['dias']) == 30, len(r.context['dias']))
    t('o dia 9, sem lançamento, ainda está zerado', dias[date(2026, 6, 9)].saldo == D('0.00'),
      dias[date(2026, 6, 9)].saldo)
    t('o dia 13, sem lançamento, segura o saldo do dia 12',
      dias[date(2026, 6, 13)].saldo == D('350.00'), dias[date(2026, 6, 13)].saldo)
    t('o último dia do mês fecha em 350', dias[date(2026, 6, 30)].saldo == D('350.00'),
      dias[date(2026, 6, 30)].saldo)
    t('a legenda diz a regra nova',
      'Valor real + Diferença − Depósito' in html, '')
    t('e explica por que a diferença entra', 'ainda não contou' in html)

    print('\n-- salvando um dia pela tela --')
    r = c.post(f'/contagem-caixa/loja/{loja.id}/salvar/',
               {'data': '2026-06-13', 'valor_real': '90,00'})
    dados = r.json()
    t('o salvar responde ok', dados.get('ok') is True, dados)
    t('sem SAP, o dia contado não inventa saldo', dados.get('saldo') == '350.00', dados.get('saldo'))
    t('e a diferença mostra a sobra', dados.get('diferenca') == '-90.00', dados.get('diferenca'))

    t('salvar um dia não cria linha para os outros',
      not ContagemCaixaDia.objects.filter(loja=loja, data=date(2026, 6, 14)).exists())

    r = c.post(f'/contagem-caixa/loja/{loja.id}/salvar/',
               {'data': '2026-06-12', 'valor_real': '300,00', 'deposito': '100,00'})
    dados = r.json()
    t('diminuir o depósito do dia 12 sobe o saldo dele',
      dados.get('saldo') == '650.00', dados.get('saldo'))
    d13 = ContagemCaixaDia.objects.get(loja=loja, data=date(2026, 6, 13))
    t('e o dia 13 foi refeito junto', d13.saldo == D('650.00'), d13.saldo)
    jul.refresh_from_db()
    t('julho, com abertura fixada, não é arrastado', jul.saldo == D('1080.00'), jul.saldo)

    html = c.get(f'/contagem-caixa/loja/{loja.id}/?mes=6&ano=2026').content.decode()
    t('o saldo refeito chega na tela', 'R$ 650,00' in html)

    print('\n== O DASHBOARD ==')
    r = c.get('/contagem-caixa/')
    linha = next((x for x in r.context['por_loja'] if x['loja'].id == loja.id), None)
    t('a loja aparece no painel', linha is not None)
    t('com o saldo do último dia lançado', linha and linha['saldo'] == D('1080.00'),
      linha and linha['saldo'])
    t('e o total soma esse saldo', r.context['saldo_total'] >= D('1080.00'),
      r.context['saldo_total'])

    print('\n== A MIGRAÇÃO QUE REFAZ O QUE JÁ ESTÁ GRAVADO ==')
    # Roda a 0006 sobre os dados de verdade, aqui dentro da transação que vai
    # ser desfeita: é a prova de que, depois do migrate, o saldo das lojas para
    # de ser R$ 0,00 — e de quanto ele passa a ser.
    import importlib

    migracao = importlib.import_module('contagem_caixa.migrations.0006_saldo_com_diferenca')
    from django.apps import apps as registro

    com_abertura = set(SaldoInicialMes.objects.values_list('loja_id', flat=True))
    antes_zerados = (ContagemCaixaDia.objects.filter(saldo=D('0.00'))
                     .exclude(valor_sap=D('0.00')).count())
    migracao.refazer_saldos(registro, None)

    print(f'    dias que estavam com saldo zerado tendo SAP: {antes_zerados}')
    depois_zerados = (ContagemCaixaDia.objects.filter(saldo=D('0.00'))
                      .exclude(valor_sap=D('0.00')).count())
    t('a migração tira o saldo do zero', depois_zerados < antes_zerados,
      (antes_zerados, depois_zerados))

    conferidas = 0
    reais = (ContagemCaixaDia.objects
             .exclude(loja_id__in=com_abertura | {loja.id})
             .order_by('loja_id').values_list('loja_id', 'loja__name').distinct())
    for loja_id, nome in list(reais)[:6]:
        linhas = list(ContagemCaixaDia.objects.filter(loja_id=loja_id).order_by('data'))
        esperado = sum((x.entrada - (x.deposito or D('0.00')) for x in linhas), D('0.00'))
        ultimo = linhas[-1]
        t(f'{nome[:30]}: fecha na soma das entradas menos os depósitos',
          ultimo.saldo == esperado, (ultimo.saldo, esperado))
        print(f'       {len(linhas)} dias, saldo final R$ {ultimo.saldo}')
        conferidas += 1
    t('conferiu lojas de verdade', conferidas >= 3, conferidas)

    print('\n== O QUE ESTAVA ERRADO ANTES ==')
    velho = ContagemCaixaDia(loja=loja, data=date(2026, 6, 20), valor_sap=D('752.00'))
    t('dia com entrada e sem contagem: a regra antiga deixava o saldo parado',
      (velho.valor_real or D('0.00')) - (velho.deposito or D('0.00')) == D('0.00'))
    t('e a nova sobe os R$ 752 que entraram',
      velho.calcular_saldo(D('0.00')) == D('752.00'), velho.calcular_saldo(D('0.00')))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
