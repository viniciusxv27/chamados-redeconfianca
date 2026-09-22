"""Simulador, visão de consultor: checkbox "Acelerador de Microindicadores".

Pedido: "em /simulator/ (na visão de consultor): Deve adicionar um checkbox
'Acelerador de Microindicadores', quando tiver marcado, deve somar 10% a mais que
o ganho total".

Marcado, o acelerador vale 10% do ganho total (comissão + PDV + Hunter + bônus
6/7) e aparece como cartão próprio no resumo; o ganho total vira x1,10 e o valor
de antes fica em ``totals['ganho_total_sem_acelerador']``. Desmarcado (o padrão),
os números são exatamente os de antes. O checkbox viaja na query string como os
níveis Hunter (``?acelerador=1``). Gerente, coordenador, "A parte" e as médias da
rede (/users/commission/projecao/) não recebem o flag.

O realizado do MySQL e as metas do Power BI são trocados por dublês; planilhas e
fatores são os reais (só leitura). Cache em memória (nada vai ao Redis) e tudo
que é gravado roda numa transação desfeita no fim.
"""
import contextlib
import inspect
import os
import re
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.db import transaction
from django.test import Client, RequestFactory
from django.test.utils import override_settings, setup_test_environment

from simulator import averages, services as sv, views
from simulator.templatetags.simulator_tags import brl
from users.models import Sector, User

setup_test_environment()   # response.context nas respostas do Client

ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def falso_mysql(vendor='', pdv='', pdvs=None, coord_name='', year=None, month=None):
    """Realizado fixo (120% de METAS): o teste não depende do dia nem do banco de vendas."""
    return {'movel': 12000.0, 'fixa': 1800.0, 'fixa_qty': 12.0, 'smartphones': 9600.0, 'eletronicos': 3600.0,
            'essenciais': 2400.0, 'seguros': 1440.0, 'sva': 1080.0}


METAS = {'movel': 10000.0, 'fixa': 10.0, 'smartphones': 8000.0, 'eletronicos': 3000.0, 'essenciais': 2000.0,
         'seguros': 1200.0, 'sva': 900.0}
# O que a pessoa digitaria no modo Simulador (sim__<pilar>__<campo>).
ENTRADAS = {'movel__meta': '10000', 'movel__real': '13000', 'movel__realpdv': '130000',
            'fixa__meta': '10', 'fixa__qty': '13', 'fixa__receita': '1950', 'fixa__qtypdv': '130',
            'smartphones__meta': '8000', 'smartphones__real': '10400', 'smartphones__realpdv': '104000',
            'eletronicos_a__meta': '3000', 'eletronicos_a__real': '3900', 'eletronicos_a__realpdv': '39000',
            'essenciais_a__meta': '2000', 'essenciais_a__real': '2600', 'essenciais_a__realpdv': '26000',
            'seguros__meta': '1200', 'seguros__real': '1560', 'seguros__realpdv': '15600',
            'sva__meta': '900', 'sva__real': '1170', 'sva__realpdv': '11700'}
HUNTERS = {'movel': 2, 'fixa': 3, 'smartphones': 0, 'eletronicos': 2, 'essenciais': 0, 'seguros': 3, 'sva': 0}
PARCELAS = ('total_with_pdv', 'hunter2', 'hunter3', 'bonus_6_7')

chamadas = []


def simulacao_falsa(*args, **kwargs):
    """Resultado pronto de outro papel, guardando com que argumentos a view chamou."""
    chamadas.append(kwargs)
    return {'user_name': 'ZZ Outro Papel', 'first_name': 'ZZ', 'pdv': 'ZZ', 'coordinator': 'ZZ',
            'view_mode': 'projecao', 'base_salary': 0.0,
            'rows': [{'key': 'movel', 'label': 'Móvel', 'meta': 1000.0, 'proj': 800.0, 'attainment': 0.8,
                      'commission_rate': 0.01, 'commission_value': 8.0, 'premium_rate': 0.0, 'premium_value': 0.0,
                      'total_individual': 8.0, 'pdv_premium_value': 0.0, 'total_with_pdv': 8.0,
                      'hunter2_value': 0.0, 'hunter3_value': 0.0}],
            'totals': {'total_with_pdv': 8.0, 'total_commission': 8.0, 'hunter2': 0.0, 'hunter3': 0.0,
                       'bonus_6_7': 0.0, 'ganho_total': 184.0}}


def sem_espaco(html):
    return re.sub(r'\s+', ' ', html)


with override_settings(CACHES={
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-acelerador'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-acelerador-local'},
}), mock.patch.object(sv, 'get_realized_sales_from_mysql', side_effect=falso_mysql), \
        mock.patch.object(sv, 'get_metas_from_power_bi', return_value=METAS), \
        mock.patch.object(views, 'realized_prefetch', lambda *a, **k: contextlib.nullcontext()):

    marcador = transaction.atomic()
    marcador.__enter__()
    try:
        loja = Sector.objects.create(name='ZZ Loja Acelerador')

        def novo(apelido, hierarquia='PADRAO'):
            # Sobrenome sem palavra comum: find_row_by_name casa por trecho do nome.
            return User.objects.create_user(
                username=f'zzacel.{apelido}', email=f'zzacel.{apelido}@exemplo-teste.local', password='S3nha!teste',
                first_name='Zzacelx', last_name=f'Qwv{apelido.title()}', sector=loja, hierarchy=hierarquia)

        consultor = novo('consultor')
        gerente = novo('gerente')
        outro = novo('outro')
        superadmin = novo('superadmin', 'SUPERADMIN')   # sem is_superuser: vale a hierarquia
        fatores = sv.get_factor_set(sv.ROLE_CONSULTOR).data

        print('== A CONTA ==')
        t('o percentual é uma constante nomeada de 10%', sv.ACELERADOR_MICROINDICADORES_RATE == 0.10)
        padrao = inspect.signature(sv.compute_consultor_simulation).parameters['acelerador_microindicadores'].default
        t('o acelerador vem desligado por padrão', padrao is False, padrao)
        for modo in (sv.VIEW_REALIZADO, sv.VIEW_PROJECAO, sv.VIEW_SIMULADOR):
            sem = sv.compute_consultor_simulation(consultor, fatores, HUNTERS, view_mode=modo, simulator_inputs=ENTRADAS)
            desl = sv.compute_consultor_simulation(consultor, fatores, HUNTERS, view_mode=modo, simulator_inputs=ENTRADAS,
                                                   acelerador_microindicadores=False)
            com = sv.compute_consultor_simulation(consultor, fatores, HUNTERS, view_mode=modo, simulator_inputs=ENTRADAS,
                                                  acelerador_microindicadores=True)
            ts, tc = sem['totals'], com['totals']
            conta_antiga = ts['total_with_pdv'] + ts['hunter2'] + ts['hunter3'] + ts['bonus_6_7']
            t(f'{modo}: a simulação tem ganho, Hunter e bônus 6/7 de verdade (o teste mede alguma coisa)',
              ts['total_with_pdv'] > 0 and ts['hunter2'] > 0 and ts['hunter3'] > 0 and ts['bonus_6_7'] > 0, ts)
            t(f'{modo}: desmarcado, o ganho total é a conta de antes, sem nenhum centavo a mais',
              ts['ganho_total'] == conta_antiga and ts['acelerador_microindicadores'] == 0.0
              and ts['ganho_total_sem_acelerador'] == ts['ganho_total'] and sem['acelerador_microindicadores'] is False,
              (ts['ganho_total'], conta_antiga))
            t(f'{modo}: passar False explícito dá o mesmo resultado do padrão', desl == sem)
            t(f'{modo}: marcado, o acelerador é 10% do ganho total',
              abs(tc['acelerador_microindicadores'] - 0.10 * ts['ganho_total']) < 1e-9, tc)
            t(f'{modo}: e o ganho total vira x1,10 (a soma das parcelas na tela fecha)',
              abs(tc['ganho_total'] - 1.10 * ts['ganho_total']) < 1e-6
              and tc['ganho_total'] == tc['ganho_total_sem_acelerador'] + tc['acelerador_microindicadores'],
              (tc['ganho_total'], ts['ganho_total']))
            t(f'{modo}: o ganho sem o acelerador continua disponível nos totais',
              tc['ganho_total_sem_acelerador'] == ts['ganho_total'])
            t(f'{modo}: comissão, PDV, Hunter e bônus não mudam; as linhas por pilar também não',
              all(tc[p] == ts[p] for p in PARCELAS) and com['rows'] == sem['rows'])
            t(f'{modo}: a tela sabe que está ligado e com quanto',
              com['acelerador_microindicadores'] is True and com['acelerador_microindicadores_pct'] == 10)

        print('\n== O CHECKBOX NA QUERY STRING ==')
        rf = RequestFactory()
        for consulta, esperado in (({'acelerador': '1'}, True), ({'acelerador': 'on'}, True), ({}, False),
                                   ({'acelerador': '0'}, False), ({'acelerador': ''}, False)):
            t(f'{consulta or "sem parâmetro"} → {esperado}',
              sv.get_acelerador_from_request(rf.get('/simulator/', consulta)) is esperado)

        print('\n== QUEM NÃO É CONSULTOR NÃO RECEBE O ACELERADOR ==')
        for funcao in (sv.compute_gerente_simulation, sv.compute_coordenador_simulation, sv.compute_aparte_simulation):
            t(f'{funcao.__name__} nem aceita o flag',
              'acelerador_microindicadores' not in inspect.signature(funcao).parameters)
        chamadas.clear()
        with mock.patch.object(averages, 'compute_consultor_simulation', side_effect=simulacao_falsa):
            averages._compute_simulation(consultor, sv.ROLE_CONSULTOR, {})
        t('as médias da rede (/users/commission/projecao/) calculam o consultor sem o acelerador',
          chamadas == [{'view_mode': sv.VIEW_PROJECAO}], chamadas)

        papeis = {consultor.pk: sv.ROLE_CONSULTOR, gerente.pk: sv.ROLE_GERENTE, superadmin.pk: sv.ROLE_SUPERADMIN}
        papel_real = sv.get_user_role
        with mock.patch.object(views, 'get_user_role', side_effect=lambda u: papeis.get(u.pk) or papel_real(u)):
            print('\n== A TELA DO CONSULTOR ==')
            c = Client()
            c.force_login(consultor)
            r = c.get('/simulator/')
            html = sem_espaco(r.content.decode())
            t('abre (200) com o checkbox desmarcado', r.status_code == 200 and 'name="acelerador" id="acelerador"' in html
              and 'Acelerador de Microindicadores' in html and r.context['acelerador_microindicadores'] is False
              and 'id="acelerador" value="1" checked' not in html, r.status_code)
            t('o rótulo diz quanto soma', 'Soma 10% ao ganho total.' in html)
            r = c.get('/simulator/', {'acelerador': '1', 'hunter_movel': '2'})
            html = sem_espaco(r.content.decode())
            t('marcado e recalculado, o checkbox volta marcado (junto com o Hunter)',
              r.context['acelerador_microindicadores'] is True and 'id="acelerador" value="1" checked' in html
              and r.context['hunter_levels']['movel'] == 2)
            t('no Realizado/Projeção não há campo escondido duplicando o checkbox',
              '<input type="hidden" name="acelerador" value="1">' not in html)
            r = c.get('/simulator/', {'view': 'simulador', 'acelerador': '1', 'sim__movel__real': '100'})
            html = r.content.decode()
            t('no Simulador, "Simular" reenvia o acelerador como reenvia os níveis Hunter',
              '<input type="hidden" name="acelerador" value="1">' in html and 'name="hunter_movel"' in html)
            r = c.get('/simulator/', {'view': 'simulador', 'sim__movel__real': '100'})
            t('desmarcado, nada é reenviado', '<input type="hidden" name="acelerador"' not in r.content.decode())

            esperado_sem = sv.compute_consultor_simulation(consultor, fatores, {}, view_mode=sv.VIEW_SIMULADOR,
                                                           simulator_inputs=ENTRADAS)['totals']
            consulta = {'fragment': 'results', 'view': 'simulador', **{f'sim__{k}': v for k, v in ENTRADAS.items()}}
            html = sem_espaco(c.get('/simulator/', consulta).content.decode())
            t('resultado desmarcado: sem cartão do acelerador e com o mesmo ganho de antes',
              'Acelerador de Microindicadores' not in html and brl(esperado_sem['ganho_total']) in html
              and 'Sem o acelerador' not in html)
            t('resultado desmarcado: o resumo mantém as 5 colunas de sempre',
              'class="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 gap-4"' in html)
            html = sem_espaco(c.get('/simulator/', {**consulta, 'acelerador': '1'}).content.decode())
            ganho_com = esperado_sem['ganho_total'] + esperado_sem['ganho_total'] * sv.ACELERADOR_MICROINDICADORES_RATE
            t('resultado marcado: cartão próprio "Acelerador de Microindicadores (+10%)" com o valor em reais',
              'Acelerador de Microindicadores (+10%)' in html
              and brl(esperado_sem['ganho_total'] * sv.ACELERADOR_MICROINDICADORES_RATE) in html)
            t('resultado marcado: Ganho Total com os 10% e a linha "Sem o acelerador"',
              brl(ganho_com) in html and f'Sem o acelerador: {brl(esperado_sem["ganho_total"])}' in html,
              (brl(ganho_com), brl(esperado_sem['ganho_total'])))
            t('resultado marcado: 6 cartões em duas linhas de 3',
              'class="grid grid-cols-1 md:grid-cols-3 gap-4"' in html)

            print('\n== GERENTE E SUPERADMIN OLHANDO UM CONSULTOR ==')
            for quem, rotulo in ((gerente, 'gerente'), (superadmin, 'superadmin')):
                cg = Client()
                cg.force_login(quem)
                r = cg.get('/simulator/', {'user_id': consultor.pk, 'acelerador': '1'})
                t(f'{rotulo}: o checkbox aparece marcado na visão do consultor',
                  r.status_code == 200 and r.context['target_role'] == sv.ROLE_CONSULTOR
                  and 'id="acelerador" value="1" checked' in sem_espaco(r.content.decode()), r.status_code)
                html = sem_espaco(cg.get('/simulator/', {**consulta, 'user_id': consultor.pk,
                                                         'acelerador': '1'}).content.decode())
                t(f'{rotulo}: e o resultado do consultor sai com o acelerador',
                  'Acelerador de Microindicadores (+10%)' in html and brl(ganho_com) in html)

            print('\n== OUTROS PAPÉIS PELA TELA ==')
            cs = Client()
            cs.force_login(superadmin)
            for papel, nome_funcao in ((sv.ROLE_GERENTE, 'compute_gerente_simulation'),
                                       (sv.ROLE_COORDENADOR, 'compute_coordenador_simulation'),
                                       (sv.ROLE_APART, 'compute_aparte_simulation')):
                papeis[outro.pk] = papel
                chamadas.clear()
                with mock.patch.object(views, nome_funcao, side_effect=simulacao_falsa):
                    r = cs.get('/simulator/', {'user_id': outro.pk, 'acelerador': '1'})
                    shell = r.content.decode()
                    html = sem_espaco(cs.get('/simulator/', {'fragment': 'results', 'user_id': outro.pk,
                                                             'acelerador': '1'}).content.decode())
                t(f'{papel}: sem checkbox na tela, mesmo com ?acelerador=1 na URL',
                  r.status_code == 200 and 'name="acelerador"' not in shell, r.status_code)
                t(f'{papel}: o cálculo é chamado sem o flag', chamadas and all(
                    'acelerador_microindicadores' not in kw for kw in chamadas), chamadas)
                t(f'{papel}: resultado sem cartão do acelerador e com o ganho intacto',
                  'Acelerador de Microindicadores' not in html and brl(184.0) in html)
    finally:
        transaction.set_rollback(True)
        marcador.__exit__(None, None, None)
        print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
