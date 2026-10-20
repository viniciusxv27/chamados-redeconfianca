"""Vendas (/vendas/) por função liberada pelo SUPERADMIN (20/10/2026).

Pedido: "Vendas" no menu Gestão Comercial, só para o SUPERADMIN e para quem ele
liberar, com cada parte do módulo liberada à parte (ver, lançar, criar produto…).

Cobre: menu e telas barrados para quem não foi liberado; SUPERADMIN vê tudo e a
aba Acessos; liberar/editar/suspender/tirar (com histórico); funções exigem
"Ver módulo"; cada parte barrada no servidor (lançar, exportar, clientes,
sincronizar, criar/importar produto, parametrização, APIs JSON); recorte
"minhas vendas" × "rede inteira"; botões somem para quem não tem a função.

Transação desfeita no fim; nada sai para a rede.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-vd'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-vd2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from users.models import Sector
from vendas import permissions as pm
from vendas.models import AcessoVendas, ItemPreco, RegistroAlteracao, Venda

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


def sem_rede(*a, **k):
    raise AssertionError('o teste tentou sair para a rede')


def html(r):
    return r.content.decode('utf-8', 'ignore')


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch('vendas.middleware.disparar_se_esta_na_hora', lambda: None):
        loja = Sector.objects.create(name='Loja ZZ Vendas')
        outra = Sector.objects.create(name='Loja ZZ Outra')
        chefe = User.objects.create_user(username='zzz.vdchefe', email='zzz.vdchefe@exemplo-teste.local',
                                         password='S3nha!teste', first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN')
        vend = User.objects.create_user(username='zzz.vdvend', email='zzz.vdvend@exemplo-teste.local',
                                        password='S3nha!teste', first_name='ZZ', last_name='Vendedora', hierarchy='PADRAO',
                                        sector=loja)
        nada = User.objects.create_user(username='zzz.vdnada', email='zzz.vdnada@exemplo-teste.local',
                                        password='S3nha!teste', first_name='ZZ', last_name='Sem', hierarchy='ADMIN',
                                        sector=loja)
        cc, cv, cn = Client(), Client(), Client()
        cc.force_login(chefe)
        cv.force_login(vend)
        cn.force_login(nada)
        v_minha = Venda.objects.create(loja=loja, vendedor=vend, created_by=vend, data_venda=timezone.now())
        v_outro = Venda.objects.create(loja=outra, vendedor=chefe, created_by=chefe, data_venda=timezone.now())

        print('— Sem liberação')
        r = cn.get('/')
        t('menu sem "Vendas" para quem não foi liberado', "url 'vendas:dashboard'" not in html(r)
          and 'href="/vendas/"' not in html(r), r.status_code)
        r = cn.get('/vendas/')
        t('/vendas/ barrado (volta para a home)', r.status_code == 302 and r.url == '/', getattr(r, 'url', ''))
        for url in ('/vendas/nova/', '/vendas/clientes/', '/vendas/precos/', '/vendas/parametros/', '/vendas/acessos/',
                    f'/vendas/{v_minha.pk}/', '/vendas/exportar/'):
            r = cn.get(url)
            t(f'{url} barrado', r.status_code == 302, r.status_code)
        r = cn.get('/vendas/api/cliente/?cpf=52998224725')
        t('API do cliente barrada (403 JSON)', r.status_code == 403 and r.json()['ok'] is False)
        r = cn.post('/vendas/nova/', data='{}', content_type='application/json')
        t('lançar por JSON barrado com JSON', r.status_code == 403 and 'erros' in r.json())

        print('— SUPERADMIN')
        r = cc.get('/')
        t('menu com "Vendas" para o SUPERADMIN', 'href="/vendas/"' in html(r))
        r = cc.get('/vendas/')
        t('dashboard abre e vê a rede', r.status_code == 200 and 'Toda a empresa' in html(r))
        t('aba Acessos aparece', '/vendas/acessos/' in html(r))
        for url in ('/vendas/nova/', '/vendas/clientes/', '/vendas/precos/', '/vendas/parametros/', '/vendas/acessos/',
                    f'/vendas/{v_minha.pk}/', '/vendas/precos/novo/', '/vendas/precos/importar/'):
            r = cc.get(url)
            t(f'{url} abre', r.status_code == 200, r.status_code)

        print('— Liberar só "Ver módulo"')
        r = cc.post('/vendas/acessos/', {'user': vend.pk, 'permissoes': ['modulo.ver'], 'ativo': 'on'})
        a = AcessoVendas.objects.get(user=vend)
        t('acesso gravado', a.permissoes == ['modulo.ver'] and a.ativo and a.concedido_por == chefe)
        t('histórico da liberação', RegistroAlteracao.objects.filter(tipo='ACESSO', objeto_id=vend.pk, campo='permissoes').exists())
        cv = Client()
        cv.force_login(vend)
        r = cv.get('/')
        t('menu com "Vendas" para a liberada', 'href="/vendas/"' in html(r))
        r = cv.get('/vendas/')
        h = html(r)
        t('entra e vê só as dela', r.status_code == 200 and f'/vendas/{v_minha.pk}/' in h and f'/vendas/{v_outro.pk}/' not in h)
        t('sem botão Nova venda / Exportar / Clientes / Parametrização / Acessos',
          '/vendas/nova/' not in h and 'id="vdExportarMenu"' not in h and '/vendas/clientes/' not in h
          and '/vendas/parametros/' not in h and '/vendas/acessos/' not in h)
        t('venda de outro pela URL dá 404', cv.get(f'/vendas/{v_outro.pk}/').status_code == 404)
        t('tabela de produtos abre', cv.get('/vendas/precos/').status_code == 200)
        h = html(cv.get('/vendas/precos/'))
        t('sem Importar / Novo item', '/vendas/precos/importar/' not in h and '/vendas/precos/novo/' not in h)
        for url in ('/vendas/nova/', '/vendas/clientes/', '/vendas/parametros/', '/vendas/acessos/', '/vendas/precos/novo/',
                    '/vendas/precos/importar/'):
            r = cv.get(url)
            t(f'{url} barrado (volta para Vendas)', r.status_code == 302 and r.url == '/vendas/', getattr(r, 'url', ''))
        r = cv.get('/vendas/?export=csv&tipo=produto')
        t('exportar pelo dashboard barrado', r.status_code == 302)
        r = cv.post('/vendas/parametros/vivo-mais/', {'percentual': '50'})
        t('POST na parametrização barrado', r.status_code == 302 and r.url == '/vendas/')
        n = ItemPreco.objects.count()
        cv.post('/vendas/precos/novo/', {'nome': 'ZZ item', 'categoria': 'ZZ'})
        t('criar produto por POST barrado', ItemPreco.objects.count() == n)

        print('— Liberar as partes')
        cc.post('/vendas/acessos/', {'user': vend.pk, 'ativo': 'on',
                                     'permissoes': ['vendas.lancar', 'vendas.exportar', 'produtos.criar', 'clientes.sincronizar']})
        a.refresh_from_db()
        t('funções completam "Ver módulo" e "Consultar clientes"',
          set(a.permissoes) == {'modulo.ver', 'vendas.lancar', 'vendas.exportar', 'produtos.criar',
                                'clientes.sincronizar', 'clientes.ver'}, a.permissoes)
        cv = Client()
        cv.force_login(vend)
        h = html(cv.get('/vendas/'))
        t('agora com Nova venda, Exportar e Clientes', '/vendas/nova/' in h and 'id="vdExportarMenu"' in h and '/vendas/clientes/' in h)
        t('ainda sem Parametrização / Acessos', '/vendas/parametros/' not in h and '/vendas/acessos/' not in h)
        r = cv.get('/vendas/nova/')
        t('tela de venda abre', r.status_code == 200)
        t('PDV fixo (sem escolher loja/vendedor)', 'id="vdLoja"' not in html(r) and 'id="vdVendedor"' not in html(r))
        r = cv.get('/vendas/exportar/?tipo=produto&export=csv')
        t('exporta só as dela', r.status_code == 200 and str(v_outro.pk) not in [l.split(';')[0].split(',')[0] for l in r.content.decode('utf-8-sig').splitlines()[1:]])
        t('API do cliente liberada', cv.get('/vendas/api/cliente/?cpf=52998224725').status_code == 200)
        h = html(cv.get('/vendas/precos/'))
        t('Novo item aparece, Importar não', '/vendas/precos/novo/' in h and '/vendas/precos/importar/' not in h)
        cv.post('/vendas/precos/novo/', {'nome': 'ZZ item', 'categoria': 'ZZ'})
        t('cria produto', ItemPreco.objects.filter(nome='ZZ item').exists())
        h = html(cv.get('/vendas/clientes/'))
        t('clientes da loja com botão de atualizar', 'Clientes da sua loja' in h and 'vdSinc' in h)
        with mock.patch('vendas.vivogo.sincronizar', return_value='0 novos'):
            r = cv.post('/vendas/clientes/sincronizar/')
        t('sincronizar liberado', r.status_code == 302 and r.url == '/vendas/clientes/')

        print('— Lançar por outra loja e ver a rede')
        cc.post('/vendas/acessos/', {'user': vend.pk, 'ativo': 'on',
                                     'permissoes': ['vendas.lancar_outros', 'rede.ver']})
        cv = Client()
        cv.force_login(vend)
        h = html(cv.get('/vendas/nova/'))
        t('escolhe loja e vendedor', 'id="vdLoja"' in h and 'id="vdVendedor"' in h)
        h = html(cv.get('/vendas/'))
        t('vê a rede inteira', 'Toda a empresa' in h and f'/vendas/{v_outro.pk}/' in h)
        t('deixou de exportar (tirado na edição)', 'id="vdExportarMenu"' not in h)

        print('— Suspender e tirar')
        cc.post('/vendas/acessos/', {'user': vend.pk, 'ativo': 'off', 'permissoes': ['modulo.ver']})
        cv = Client()
        cv.force_login(vend)
        t('suspenso não entra', cv.get('/vendas/').status_code == 302)
        t('suspenso sem item no menu', 'href="/vendas/"' not in html(cv.get('/')))
        cc.post('/vendas/acessos/', {'user': vend.pk, 'remover': '1'})
        t('tirar acesso apaga a liberação', not AcessoVendas.objects.filter(user=vend).exists())
        t('histórico guarda a retirada', RegistroAlteracao.objects.filter(tipo='ACESSO', objeto_id=vend.pk,
                                                                            depois='(sem acesso)').exists())
        r = cc.post('/vendas/acessos/', {'user': nada.pk})
        t('sem função marcada não libera', not AcessoVendas.objects.filter(user=nada).exists())
        r = cc.get('/vendas/acessos/')
        t('tela de acessos mostra o histórico', 'Histórico de liberações' in html(r))
        h = html(cc.get('/vendas/parametros/'))
        t('histórico da parametrização não mistura acessos', 'Funções' not in h or 'ZZ Vendedora' not in h)
        cn2 = Client()
        cn2.force_login(nada)
        t('só SUPERADMIN abre /vendas/acessos/', cn2.get('/vendas/acessos/').status_code == 302)
        t('pode_no_template sem tabela não quebra o menu', pm.pode_no_template(None) == {'acessos': False})
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
