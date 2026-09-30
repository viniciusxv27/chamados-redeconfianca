"""Contestação: os três botões de exportar do /contestacao/dashboard.

Pedido: "verificar os 3 botões de exportar relatório, se eles estão
respeitando o filtro e tirando o relatório corretamente".

Estavam tirando o relatório certo, mas **ignorando o filtro de mês**: com
setembro escolhido na tela (285 contestações, 597 vendas na base), os três
arquivos saíam com o histórico inteiro — 2253, 6721 e 410 linhas. Os links
nem carregavam o `?month=`, e as views não liam o parâmetro.

O que este teste cobre:

- os três links da tela levam o mês escolhido (e só quando há um);
- cada CSV sai com o mesmo recorte do dashboard — contagem e soma de receita;
- o nome do arquivo diz o mês, para não misturar arquivos na pasta;
- mês inválido não quebra: sai tudo, como na tela;
- o escopo por setor continua valendo nos três (gerente leva só a sua loja);
- quem não gerencia contestação não exporta;
- o arquivo abre no Excel pt-BR: BOM, separador `;` e o cabeçalho de sempre.

Roda dentro de uma transação desfeita.
"""
import csv
import io
import os
import sys
from datetime import datetime, timezone as tz_utc
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-export'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-export-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from contestacao.models import (Contestation, ExclusionRecord, ExclusionSyncBatch, TipoBase)
from users.models import Sector

User = get_user_model()
ok = fail = 0
VENDAS = '/contestacao/dashboard/exportar-vendas/'
TODAS = '/contestacao/dashboard/exportar-todas-vendas/'
RELATORIO = '/contestacao/dashboard/exportar-relatorio/'


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
    centro = Sector.objects.create(name='ZZ Centro Export')
    norte = Sector.objects.create(name='ZZ Norte Export')
    chefe = User.objects.create_user(
        username='zzex.chefe', email='zzex.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    gerente = User.objects.create_user(
        username='zzex.gerente', email='zzex.gerente@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Gerente', hierarchy='ADMIN', sector=centro)
    padrao = User.objects.create_user(
        username='zzex.padrao', email='zzex.padrao@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Padrao', hierarchy='PADRAO', sector=centro)

    lote = ExclusionSyncBatch.objects.create(record_type=TipoBase.EXCLUSAO, record_count=6,
                                             created_by=chefe)

    def venda(n, receita, filial, ano, mes):
        r = ExclusionRecord.objects.create(
            sync_batch=lote, filial=filial, vendedor=f'ZZ Vendedor {n}', receita=Decimal(receita),
            pilar='FIBRA', numero_venda=f'ZZX{n}', data_venda='01/09/2026',
            nome_cliente=f'ZZ Cliente {n}', record_type=TipoBase.EXCLUSAO)
        # imported_at é auto_now_add: o mês do teste se escreve depois.
        ExclusionRecord.objects.filter(pk=r.pk).update(
            imported_at=datetime(ano, mes, 10, 12, 0, tzinfo=tz_utc.utc))
        r.refresh_from_db()
        return r

    def contestar(registro, ano, mes, quem=None, status='pending'):
        c = Contestation.objects.create(exclusion=registro, requester=quem or gerente,
                                        reason='ZZ motivo do teste', status=status)
        Contestation.objects.filter(pk=c.pk).update(
            created_at=datetime(ano, mes, 15, 12, 0, tzinfo=tz_utc.utc))
        c.refresh_from_db()
        return c

    # Agosto: 2 vendas no Centro (1 contestada) e 1 no Norte (contestada).
    a1 = venda(1, '100.00', 'ZZ CENTRO EXPORT', 2026, 8)
    venda(2, '200.00', 'ZZ CENTRO EXPORT', 2026, 8)
    a3 = venda(3, '300.00', 'ZZ NORTE EXPORT', 2026, 8)
    contestar(a1, 2026, 8)
    contestar(a3, 2026, 8)

    # Setembro: 2 no Centro (as duas contestadas, uma delas duas vezes) e 1 no Norte.
    s4 = venda(4, '400.00', 'ZZ CENTRO EXPORT', 2026, 9)
    s5 = venda(5, '500.00', 'ZZ CENTRO EXPORT', 2026, 9)
    venda(6, '600.00', 'ZZ NORTE EXPORT', 2026, 9)
    contestar(s4, 2026, 9, status='accepted')
    contestar(s5, 2026, 9)
    contestar(s5, 2026, 9, status='rejected')          # a mesma venda, de novo

    c = Client(); c.force_login(chefe)

    def baixar(url, cliente=None):
        r = (cliente or c).get(url)
        corpo = r.content.decode('utf-8-sig') if r.status_code == 200 else ''
        linhas = list(csv.reader(io.StringIO(corpo), delimiter=';')) if corpo else []
        return r, linhas

    def soma(linhas, coluna):
        i = linhas[0].index(coluna)
        return sum(Decimal(l[i] or 0) for l in linhas[1:])

    def meus(linhas, coluna='Nº da Venda'):
        """Só as linhas deste teste: o banco de dev tem os dados reais junto."""
        i = linhas[0].index(coluna)
        return sorted(l[i] for l in linhas[1:] if l[i].startswith('ZZX'))

    print('== SETEMBRO: O CSV SAI COM O QUE ESTÁ NA TELA ==')
    painel = c.get('/contestacao/dashboard/?month=2026-09').context

    r, vendas = baixar(VENDAS + '?month=2026-09')
    t('vendas contestadas: uma linha por contestação do mês, como no dashboard',
      len(vendas) - 1 == painel['total_enviado'],
      (len(vendas) - 1, painel['total_enviado']))
    t('as três contestações de setembro deste teste estão lá',
      meus(vendas) == ['ZZX4', 'ZZX5', 'ZZX5'], meus(vendas))
    t('e a receita bate com a do dashboard',
      soma(vendas, 'RECEITA') == painel['receita_contestada'],
      (soma(vendas, 'RECEITA'), painel['receita_contestada']))
    t('o nome do arquivo diz o mês',
      'vendas_contestadas_2026-09.csv' in r['Content-Disposition'], r['Content-Disposition'])

    r, todas = baixar(TODAS + '?month=2026-09')
    t('todas as vendas: a base do mês inteira, como no dashboard',
      len(todas) - 1 == painel['total_na_base'], (len(todas) - 1, painel['total_na_base']))
    t('e a receita da base bate', soma(todas, 'RECEITA') == painel['receita_na_base'],
      (soma(todas, 'RECEITA'), painel['receita_na_base']))
    t('a base de setembro deste teste sai inteira, contestada ou não',
      meus(todas) == ['ZZX4', 'ZZX5', 'ZZX6'], meus(todas))
    i_num, i_cont = todas[0].index('Nº da Venda'), todas[0].index('Contestada')
    marcadas = {l[i_num]: l[i_cont] for l in todas[1:] if l[i_num].startswith('ZZX')}
    t('marcando quais foram contestadas',
      marcadas == {'ZZX4': 'Sim', 'ZZX5': 'Sim', 'ZZX6': 'Nao'}, marcadas)
    t('a venda contestada duas vezes aparece uma vez só (a mais recente)',
      meus(todas).count('ZZX5') == 1)
    t('o nome do arquivo diz o mês', 'todas_vendas_2026-09.csv' in r['Content-Disposition'])

    r, rel = baixar(RELATORIO + '?month=2026-09')
    t('e a soma do relatório bate com o dashboard',
      soma(rel, 'Total Vendas') == painel['total_enviado']
      and soma(rel, 'Receita Total') == painel['receita_contestada'],
      (soma(rel, 'Total Vendas'), soma(rel, 'Receita Total')))
    t('o nome do arquivo diz o mês', 'relatorio_contestacoes_2026-09.csv' in r['Content-Disposition'])

    print('\n== AGOSTO: OUTRO MÊS, OUTRO ARQUIVO ==')
    painel8 = c.get('/contestacao/dashboard/?month=2026-08').context
    _, vendas8 = baixar(VENDAS + '?month=2026-08')
    _, todas8 = baixar(TODAS + '?month=2026-08')
    _, rel8 = baixar(RELATORIO + '?month=2026-08')
    t('as contestações de agosto batem com o dashboard de agosto',
      len(vendas8) - 1 == painel8['total_enviado'], (len(vendas8) - 1, painel8['total_enviado']))
    t('a base de agosto também', len(todas8) - 1 == painel8['total_na_base'],
      (len(todas8) - 1, painel8['total_na_base']))
    t('e o relatório de agosto', soma(rel8, 'Total Vendas') == painel8['total_enviado'],
      (soma(rel8, 'Total Vendas'), painel8['total_enviado']))
    t('sai o que é de agosto', meus(vendas8) == ['ZZX1', 'ZZX3'], meus(vendas8))
    t('e setembro não vaza em agosto',
      'ZZX4' not in meus(vendas8) and 'ZZX5' not in meus(vendas8)
      and meus(todas8) == ['ZZX1', 'ZZX2', 'ZZX3'], meus(todas8))

    print('\n== SEM FILTRO E COM FILTRO ESTRAGADO ==')
    painel_tudo = c.get('/contestacao/dashboard/').context
    _, tudo = baixar(VENDAS)
    t('sem mês, sai o histórico inteiro (o mesmo do dashboard sem filtro)',
      len(tudo) - 1 == painel_tudo['total_enviado'],
      (len(tudo) - 1, painel_tudo['total_enviado']))
    t('com as cinco contestações deste teste, dos dois meses',
      meus(tudo) == ['ZZX1', 'ZZX3', 'ZZX4', 'ZZX5', 'ZZX5'], meus(tudo))
    for ruim in ('bobagem', '2026-13', '2026-00', '', '9999-99'):
        r, linhas = baixar(f'{VENDAS}?month={ruim}')
        if not (r.status_code == 200 and len(linhas) == len(tudo)
                and 'vendas_contestadas.csv' in r['Content-Disposition']):
            t(f'mês inválido ({ruim!r}) sai como "todos"', False,
              (r.status_code, len(linhas) - 1, r['Content-Disposition']))
            break
    else:
        t('mês inválido sai como "todos", sem quebrar (5 casos)', True)

    print('\n== QUEM PODE O QUÊ ==')
    cg = Client(); cg.force_login(gerente)
    _, do_gerente = baixar(VENDAS + '?month=2026-09', cliente=cg)
    t('o gerente exporta só a loja dele', meus(do_gerente) == ['ZZX4', 'ZZX5', 'ZZX5']
      and all(l[0] == 'ZZ CENTRO EXPORT' for l in do_gerente[1:]),
      {l[0] for l in do_gerente[1:]})
    _, base_gerente = baixar(TODAS + '?month=2026-09', cliente=cg)
    t('e em todas as vendas também (a do Norte fica de fora)',
      meus(base_gerente) == ['ZZX4', 'ZZX5']
      and all(l[0] == 'ZZ CENTRO EXPORT' for l in base_gerente[1:]),
      meus(base_gerente))
    _, rel_gerente = baixar(RELATORIO + '?month=2026-09', cliente=cg)
    t('e no relatório também', {l[0] for l in rel_gerente[1:]} == {'ZZ CENTRO EXPORT'},
      {l[0] for l in rel_gerente[1:]})

    cp = Client(); cp.force_login(padrao)
    t('quem não gerencia contestação não exporta',
      all(cp.get(u).status_code == 302 for u in (VENDAS, TODAS, RELATORIO)))

    print('\n== O ARQUIVO ABRE NO EXCEL ==')
    r = c.get(VENDAS + '?month=2026-09')
    t('vem com BOM e separador ;', r.content.startswith(b'\xef\xbb\xbf') and b';' in r.content)
    t('e é anexo de CSV', 'text/csv' in r['Content-Type'] and 'attachment' in r['Content-Disposition'])
    t('o cabeçalho continua o de sempre',
      vendas[0][:4] == ['FILIAL', 'Vendedor', 'RECEITA', 'Pilar']
      and 'Status Contestacao' in vendas[0] and 'Data Criacao' in vendas[0], vendas[0][:6])
    t('o de todas as vendas traz a coluna Contestada', 'Contestada' in todas[0])
    t('e o relatório agrupa por filial, pilar, status e botão',
      rel[0] == ['Filial', 'Pilar', 'Status', 'Botao Clicado', 'Total Vendas', 'Receita Total'],
      rel[0])

    print('\n== A TELA LEVA O FILTRO ==')
    html = c.get('/contestacao/dashboard/?month=2026-09').content.decode()
    t('os três botões levam o mês escolhido',
      html.count('?month=2026-09') >= 3, html.count('?month=2026-09'))
    t('e a tela avisa o que vai sair no arquivo, com o mês por extenso',
      'Os arquivos saem só com setembro de 2026' in html)
    limpo = c.get('/contestacao/dashboard/').content.decode()
    t('sem filtro, os links vão limpos',
      '?month=' not in limpo and 'Os arquivos saem com todos os meses' in limpo)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
