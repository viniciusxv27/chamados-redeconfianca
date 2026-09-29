"""Simulador: tabela da rede com realizado, projeção, diferença e motivo.

Pedido: "preciso de uma visão de tabela, como na imagem anexada, de todos os
colaboradores (resultado realizado e projeção, e a diferença e o motivo)".

O que este teste cobre:

- a comparação entre as duas visões: sai pilar por pilar, do que mais pesa
  para o que menos pesa, e o acelerador (Hunter/bônus) vem marcado;
- o motivo em uma frase: sem dados, sem diferença, e os três maiores fatores
  com "e mais N" quando há mais;
- a linha de cada pessoa: realizado, projeção, diferença, e ordem pela maior
  projeção;
- erro no cálculo de uma pessoa não derruba a tabela inteira;
- o cache do dia: serve o número de ontem e manda recalcular em segundo plano
  em vez de fazer o gestor esperar;
- a porta: consultor PADRÃO não entra;
- o recorte: gerente vê a própria loja, superadmin vê a rede;
- a tela: os valores, o motivo, a conta pilar por pilar no detalhe e o total
  do rodapé;
- o link para a tabela no cabeçalho do simulador.

O motor de comissão é dublado (planilha e MySQL não são tocados). Transação
desfeita no fim.
"""
import contextlib
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-tab'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-tab-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from simulator import tabela
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


def sim(pilares, extras=None, erro=None, campo='total_with_pdv'):
    """Um resultado do simulador no formato que a tela consome.

    ``campo`` escolhe onde mora o dinheiro do pilar: o consultor usa
    ``total_with_pdv``; o coordenador e o "A parte" só têm ``commission_value``.
    ``atingimento`` é fração (0,88 = 88%), como no resto do simulador.
    """
    if erro:
        return {'error': erro, 'rows': [], 'totals': {}}
    linhas = [
        {'key': chave, 'label': rotulo, campo: valor,
         'meta': 10.0, 'proj': 12.0, 'attainment': atingimento}
        for chave, rotulo, valor, atingimento in pilares
    ]
    totais = {'ganho_total': sum(l[campo] for l in linhas) + sum((extras or {}).values())}
    totais.update(extras or {})
    return {'rows': linhas, 'totals': totais, 'pdv': 'MONTSERRAT', 'coordinator': 'CARLOS'}


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== A COMPARAÇÃO ENTRE AS DUAS VISÕES ==')
    realizado = sim([('aparelho', 'Aparelho puro', 100.0, 0.60),
                     ('eletro', 'Eletrônicos', 50.0, 0.40),
                     ('essenciais', 'Essenciais', 30.0, 0.90)],
                    {'hunter2': 0.0, 'hunter3': 0.0})
    projecao = sim([('aparelho', 'Aparelho puro', 180.0, 1.05),
                    ('eletro', 'Eletrônicos', 70.0, 0.80),
                    ('essenciais', 'Essenciais', 30.0, 0.95)],
                   {'hunter2': 40.0, 'hunter3': 0.0})

    fatores = tabela.comparar(realizado, projecao)
    t('sai um fator por pilar que mudou', len(fatores) == 3, [f['label'] for f in fatores])
    t('do que mais pesa para o que menos pesa',
      [f['label'] for f in fatores] == ['Aparelho puro', 'Hunter II', 'Eletrônicos'],
      [f['label'] for f in fatores])
    t('o pilar sem mudança fica de fora',
      all(f['label'] != 'Essenciais' for f in fatores))
    t('com o realizado e a projeção de cada um',
      fatores[0]['realizado'] == 100.0 and fatores[0]['projecao'] == 180.0 and fatores[0]['delta'] == 80.0,
      fatores[0])
    t('o acelerador vem marcado como acelerador',
      next(f for f in fatores if f['label'] == 'Hunter II')['tipo'] == 'extra')
    t('e o pilar vem com o atingimento projetado (fração, como no simulador)',
      fatores[0]['tipo'] == 'pilar' and fatores[0]['atingimento'] == 1.05)
    t('a soma dos fatores explica a diferença inteira',
      abs(sum(f['delta'] for f in fatores) - 140.0) < 0.01,
      sum(f['delta'] for f in fatores))

    t('diferença abaixo de R$ 1 não é motivo de nada',
      tabela.comparar(sim([('a', 'Pilar A', 100.0, 0.50)]),
                      sim([('a', 'Pilar A', 100.40, 0.50)])) == [])
    t('pilar que cai também aparece (com delta negativo)',
      tabela.comparar(sim([('a', 'Pilar A', 100.0, 0.50)]),
                      sim([('a', 'Pilar A', 60.0, 0.30)]))[0]['delta'] == -40.0)

    # Coordenador e "A parte" não têm prêmio de PDV: o dinheiro do pilar só
    # existe em `commission_value`. Sem ler esse campo, a tela dizia
    # "diferença de arredondamento" para eles (visto no motor de verdade).
    coord_real = sim([('movel', 'Móvel', 185.14, 0.75)], campo='commission_value')
    coord_proj = sim([('movel', 'Móvel', 647.97, 0.88)], campo='commission_value')
    fator_coord = tabela.comparar(coord_real, coord_proj)
    t('pilar que só tem commission_value também é explicado',
      len(fator_coord) == 1 and round(fator_coord[0]['delta'], 2) == 462.83, fator_coord)
    # A linha que junta eletrônicos/essenciais A+B chega com total zerado e a
    # comissão cheia no campo antigo — vale o maior dos dois.
    junta_real = {'rows': [{'key': 'eletro', 'label': 'Eletrônicos', 'total_with_pdv': 0.0,
                            'commission_value': 100.0, 'attainment': 0.9}],
                  'totals': {'ganho_total': 100.0}}
    junta_proj = {'rows': [{'key': 'eletro', 'label': 'Eletrônicos', 'total_with_pdv': 0.0,
                            'commission_value': 260.0, 'attainment': 1.1}],
                  'totals': {'ganho_total': 260.0}}
    t('linha somada (A+B) não perde a comissão',
      tabela.comparar(junta_real, junta_proj)[0]['delta'] == 160.0)
    t('cálculo com erro não gera fator nenhum',
      tabela.comparar(sim([], erro='sem PDV'), projecao if False else sim([], erro='sem PDV')) == [])

    print('\n== O MOTIVO EM UMA FRASE ==')
    t('sem dados no mês é o próprio motivo',
      tabela.resumir_motivo([], False, 0.0) == 'Sem dados no mês')
    t('projeção igual ao realizado é dito assim',
      tabela.resumir_motivo([], True, 0.20) == 'Projeção igual ao realizado')
    t('quem não vendeu nada no mês é dito assim',
      tabela.resumir_motivo([], True, 0.0, zerado=True) == 'Sem venda no mês')
    frase = tabela.resumir_motivo(fatores, True, 140.0)
    t('a frase nomeia o maior fator primeiro', frase.startswith('Aparelho puro +R$ 80'), frase)
    t('com os valores em reais e o sinal', 'Hunter II +R$ 40' in frase, frase)
    muitos = fatores + [{'tipo': 'pilar', 'label': f'Pilar {i}', 'delta': 2.0,
                         'realizado': 0.0, 'projecao': 2.0, 'atingimento': 0.0}
                        for i in range(4)]
    frase_longa = tabela.resumir_motivo(muitos, True, 200.0)
    t('mais de três fatores viram "e mais N"', frase_longa.endswith('e mais 4'), frase_longa)
    t('e a frase não cresce sem limite', frase_longa.count('·') == 3, frase_longa)

    print('\n== A LINHA DE CADA PESSOA ==')
    setor = Sector.objects.create(name='ZZ Loja da Tabela')
    outro_setor = Sector.objects.create(name='ZZ Loja de Fora')
    ana = User.objects.create_user(
        username='zzt.ana', email='zzt.ana@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZT', last_name='Ana', sector=setor)
    bia = User.objects.create_user(
        username='zzt.bia', email='zzt.bia@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZT', last_name='Bia', sector=setor)
    caio = User.objects.create_user(
        username='zzt.caio', email='zzt.caio@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZT', last_name='Caio', sector=outro_setor)

    roster = [{'user': ana, 'role': 'consultor'},
              {'user': bia, 'role': 'gerente'},
              {'user': caio, 'role': 'consultor'}]

    def simular_falso(user, role, factors, view_mode, config_aparte=None):
        if user.id == caio.id:
            raise RuntimeError('planilha fora do ar')
        if user.id == bia.id:
            # Gerente sem dados no mês: as duas visões voltam com erro.
            return sim([], erro='sem PDV para a loja')
        if view_mode == 'realizado':
            return realizado
        return projecao

    with mock.patch.object(tabela, 'realized_prefetch',
                           lambda *a, **k: contextlib.nullcontext()), \
         mock.patch.object(tabela, '_simular', simular_falso):
        linhas = tabela.construir_linhas(roster)

    por_id = {l['id']: l for l in linhas}
    t('uma linha por colaborador', len(linhas) == 3, len(linhas))
    t('o realizado da pessoa', por_id[ana.id]['realizado'] == 180.0, por_id[ana.id]['realizado'])
    t('a projeção da pessoa', por_id[ana.id]['projecao'] == 320.0, por_id[ana.id]['projecao'])
    t('a diferença é projeção menos realizado', por_id[ana.id]['diferenca'] == 140.0)
    t('com o motivo montado', por_id[ana.id]['motivo'].startswith('Aparelho puro +R$ 80'),
      por_id[ana.id]['motivo'])
    t('e a loja e o coordenador da pessoa',
      por_id[ana.id]['sector'] and por_id[ana.id]['coordinator'] == 'CARLOS',
      (por_id[ana.id]['sector'], por_id[ana.id]['coordinator']))
    t('quem não tem dados no mês fica marcado',
      por_id[bia.id]['has_data'] is False and por_id[bia.id]['motivo'] == 'Sem dados no mês')
    t('e o erro do cálculo aparece na linha', 'sem PDV' in por_id[bia.id]['erro'], por_id[bia.id]['erro'])
    t('cálculo que estourou não derruba a tabela',
      por_id[caio.id]['erro'] == 'Não deu para calcular' and por_id[caio.id]['projecao'] == 0.0)
    t('a tabela vem da maior projeção para a menor',
      [l['id'] for l in linhas][0] == ana.id, [l['name'] for l in linhas])

    resumo = tabela.resumir(linhas)
    t('o resumo soma o realizado e a projeção',
      resumo['realizado'] == 180.0 and resumo['projecao'] == 320.0)
    t('e conta quem tem dados', resumo['pessoas'] == 3 and resumo['com_dados'] == 1)
    t('a média projetada ignora quem não tem dados', resumo['media_projecao'] == 320.0)

    print('\n== O CACHE DO DIA ==')
    cache.delete(tabela.DATASET_KEY)
    cache.delete(tabela.ERROR_KEY)
    pedidos = []
    with mock.patch.object(tabela, '_recalcular_em_segundo_plano',
                           lambda force_refresh=False: pedidos.append(force_refresh) or True):
        vazio = tabela.get_tabela_dataset()
        t('sem cache, a tela avisa que está montando', vazio['status'] == 'building' and vazio['rows'] == [])
        t('e o cálculo é pedido em segundo plano', pedidos == [False], pedidos)

        cache.set(tabela.DATASET_KEY, {'rows': linhas, 'generated_at': timezone.now(),
                                       'built_on': timezone.localdate()}, 60)
        pronto = tabela.get_tabela_dataset()
        t('com o cache do dia, responde na hora', pronto['status'] == 'ready' and len(pronto['rows']) == 3)
        t('e não pede recálculo à toa', pedidos == [False], pedidos)

        cache.set(tabela.DATASET_KEY, {'rows': linhas, 'generated_at': timezone.now() - timedelta(days=1),
                                       'built_on': timezone.localdate() - timedelta(days=1)}, 60)
        velho = tabela.get_tabela_dataset()
        t('dataset de ontem ainda é servido', velho['status'] == 'ready' and len(velho['rows']) == 3)
        t('marcado como desatualizado', velho['is_stale'] is True)
        t('e o recálculo vai para segundo plano', pedidos == [False, False], pedidos)

        cache.set(tabela.ERROR_KEY, True, 60)
        tabela.get_tabela_dataset()
        t('depois de falhar, espera antes de tentar de novo', pedidos == [False, False], pedidos)
        cache.delete(tabela.ERROR_KEY)

        tabela.get_tabela_dataset(force_refresh=True)
        t('o botão Recalcular força o recálculo', pedidos[-1] is True, pedidos)

    print('\n== A PORTA E O RECORTE ==')
    chefe = User.objects.create_user(
        username='zzt.chefe', email='zzt.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZT', last_name='Chefe', hierarchy='SUPERADMIN', sector=setor)
    grupo = CommunicationGroup.objects.filter(name__icontains='GERENTES').first()
    if grupo:
        grupo.members.add(bia)

    cache.set(tabela.DATASET_KEY, {'rows': linhas, 'generated_at': timezone.now(),
                                   'built_on': timezone.localdate()}, 300)

    c_ana = Client(); c_ana.force_login(ana)
    r = c_ana.get('/simulator/tabela/')
    t('consultor PADRÃO não entra', r.status_code == 302, r.status_code)

    c_chefe = Client(); c_chefe.force_login(chefe)
    r = c_chefe.get('/simulator/tabela/')
    t('superadmin abre a tabela', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('e vê a rede inteira', html.count('class="person-row') == 3, html.count('class="person-row'))

    if grupo:
        c_bia = Client(); c_bia.force_login(bia)
        r = c_bia.get('/simulator/tabela/')
        t('gerente abre a tabela', r.status_code == 200, r.status_code)
        gerente_html = r.content.decode()
        t('e vê só a própria loja', gerente_html.count('class="person-row') == 2,
          gerente_html.count('class="person-row'))
        t('a pessoa de outra loja fica de fora', 'ZZT Caio' not in gerente_html)
    else:
        print('  (sem grupo GERENTES neste banco: recorte de gerente não conferido)')

    print('\n== A TELA ==')
    t('o cabeçalho diz o que a tabela mostra',
      'Tabela da Rede' in html and 'Realizado' in html and 'Projeção' in html and 'Diferença' in html)
    t('a coluna do motivo vem preenchida', 'Aparelho puro +R$ 80' in html)
    t('cada linha leva realizado, projeção e diferença',
      'data-realizado="180.000000"' in html and 'data-projecao="320.000000"' in html
      and 'data-diferenca="140.000000"' in html,
      [p for p in html.split() if p.startswith('data-realizado')][:3])
    t('o detalhe abre a conta pilar por pilar',
      'De onde vem a diferença de ZZT Ana' in html and 'Atingimento proj.' in html)
    t('com o atingimento em percentual (fração vira %)', '105,0%' in html,
      [p for p in html.split('>') if '%' in p][:3])
    t('no celular o motivo vai para debaixo do nome',
      'motivo-mini' in html and 'lg:hidden' in html)
    t('e as colunas de dinheiro que não cabem somem',
      'hidden sm:table-cell' in html)
    t('com o acelerador identificado no detalhe', '>acelerador<' in html)
    t('quem não tem dados aparece assim na tela', 'sem dados' in html)
    t('o rodapé soma a coluna', 'foot-realizado' in html and 'foot-projecao' in html
      and 'foot-diferenca' in html)
    t('dá para filtrar por diferença', 'Só quem sobe' in html and 'Sem dados no mês' in html)
    t('e por loja, coordenador e papel',
      'sector-filter' in html and 'coordinator-filter' in html and 'role-chip' in html)
    t('dá para baixar a tabela', 'btn-csv' in html and 'tabela-rede.csv' in html)
    t('e a linha leva para o simulador da pessoa nas duas visões',
      f'/simulator/?user_id={ana.id}&view=realizado' in html
      and f'/simulator/?user_id={ana.id}&view=projecao' in html)

    print('\n== O LINK NO SIMULADOR ==')
    with mock.patch('simulator.views.realized_prefetch',
                    lambda *a, **k: contextlib.nullcontext()):
        painel = c_chefe.get('/simulator/').content.decode()
    t('o cabeçalho do simulador leva para a tabela',
      '/simulator/tabela/' in painel and 'Tabela da rede' in painel)
    with mock.patch('simulator.views.realized_prefetch',
                    lambda *a, **k: contextlib.nullcontext()):
        painel_ana = c_ana.get('/simulator/').content.decode()
    t('consultor não recebe o link', '/simulator/tabela/' not in painel_ana)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
