"""Setores: "CNPJ VIVO" e "NOME LOJAS" ligados ao ADABAS (22/10/2026).

Planilha "Acompanhamento E.A e Vivo +": a migração users/0048 preenche pelos ADABAS; /users/manage/sectors/
mostra e edita; /acess/lojas/ mostra ao lado do ADABAS (com copiar) e o "Copiar tudo" da ficha leva junto.

Transação desfeita no fim; nada sai para a rede.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sv'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sv2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from acess import servicos as sv
from acess.models import AcessoAcess
from acess.permissoes import CHAVES
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


def sem_rede(*a, **k):
    raise AssertionError('o teste tentou sair para a rede')


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede):
        adm = User.objects.create_user(username='zz.sv.adm', email='zz.sv.adm@exemplo-teste.local', password='x',
                                       first_name='ZZ', last_name='Adm', hierarchy='SUPERADMIN')
        AcessoAcess.objects.create(user=adm, permissoes=sorted(CHAVES))
        c = Client()
        c.force_login(adm)

        print('— Preenchido pela planilha')
        serra = Sector.objects.get(adabas='ESD0267-016')
        t('ADABAS ESD0267-016 ligado ao CNPJ e ao nome da Vivo', serra.cnpj_vivo == '02.558.157/1140-95'
          and serra.nome_loja_vivo == 'EA MASTERCEL SERRA VI ES', (serra.cnpj_vivo, serra.nome_loja_vivo))
        t('as 21 lojas da planilha preenchidas', Sector.objects.exclude(adabas='').exclude(cnpj_vivo='').count() >= 21)
        sp = Sector.objects.get(adabas='SPREV002-003')
        t('lojas de SP (users/0049): Carapicuíba', sp.cnpj_vivo == '09.163.602/0028-54' and sp.nome_loja_vivo == 'VIVO CARAPICUIBA SP')
        t('nenhuma loja com ADABAS sem os dados da Vivo', not Sector.objects.exclude(adabas='').filter(cnpj_vivo='').exists())

        print('— /users/manage/sectors/')
        h = c.get('/users/manage/sectors/').content.decode()
        t('lista mostra CNPJ e nome da Vivo ao lado do ADABAS', '02.558.157/1140-95' in h and 'EA MASTERCEL SERRA VI ES' in h)
        h = c.get(f'/users/manage/sectors/{serra.id}/edit/').content.decode()
        t('edição traz os dois campos preenchidos', 'name="cnpj_vivo"' in h and 'value="02.558.157/1140-95"' in h
          and 'value="EA MASTERCEL SERRA VI ES"' in h)
        loja = Sector.objects.create(name='Loja ZZ Vivo', adabas='ZZV0001-001')
        c.post(f'/users/manage/sectors/{loja.id}/edit/', {'name': loja.name, 'description': '', 'adabas': 'ZZV0001-001',
                                                         'cnpj_vivo': '12345678000195', 'nome_loja_vivo': '  ea  zz teste es '})
        loja.refresh_from_db()
        t('grava com o CNPJ formatado e o nome em maiúsculas', loja.cnpj_vivo == '12.345.678/0001-95'
          and loja.nome_loja_vivo == 'EA ZZ TESTE ES', (loja.cnpj_vivo, loja.nome_loja_vivo))
        r = c.post(f'/users/manage/sectors/{loja.id}/edit/', {'name': loja.name, 'adabas': 'ZZV0001-001',
                                                             'cnpj_vivo': '123', 'nome_loja_vivo': 'X'}, follow=True)
        loja.refresh_from_db()
        t('CNPJ incompleto é recusado e nada muda', loja.cnpj_vivo == '12.345.678/0001-95'
          and 'CNPJ Vivo: informe os 14 números' in r.content.decode())
        c.post('/users/manage/sectors/create/', {'name': 'Loja ZZ Nova Vivo', 'adabas': 'ZZV0002-001',
                                                 'cnpj_vivo': '98.765.432/0001-10', 'nome_loja_vivo': 'EA ZZ NOVA'})
        nova = Sector.objects.get(name='Loja ZZ Nova Vivo')
        t('criar setor já com os dois campos', nova.cnpj_vivo == '98.765.432/0001-10' and nova.nome_loja_vivo == 'EA ZZ NOVA')

        print('— /acess/lojas/')
        col = sv.criar_colaborador({'nome': 'ZZ Pessoa Vivo', 'cpf': '52998224725'}, loja, usuario=adm)
        h = c.get('/acess/lojas/?q=ZZ+Pessoa+Vivo').content.decode()
        t('ao lado do ADABAS: nome e CNPJ da Vivo, com copiar', 'EA ZZ TESTE ES' in h and 'CNPJ 12.345.678/0001-95' in h
          and 'data-copiar="12.345.678/0001-95"' in h and 'data-copiar="EA ZZ TESTE ES"' in h)
        t('botão de copiar ao lado do ADABAS', 'data-copiar="ZZV0001-001" title="Copiar o ADABAS"' in h)
        t('o "Copiar tudo" da ficha leva CNPJ e loja da Vivo', 'CNPJ Vivo: 12.345.678/0001-95' in col.texto_para_copiar
          and 'Loja Vivo: EA ZZ TESTE ES' in col.texto_para_copiar)
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
