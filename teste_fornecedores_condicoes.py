"""Fornecedores: preço, frete e tempo de entrega (opcionais) no cadastro e na edição (11/11/2026).

Rodar: venv/bin/python teste_fornecedores_condicoes.py
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco descartável.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-fornec-'))
(PASTA / 'zz_fornec_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3',
                          'NAME': r'{PASTA}/banco.sqlite3'}}}}


class SemMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = SemMigrations()

USE_S3 = False
MEDIA_ROOT = r'{PASTA}/media'
MEDIA_URL = '/media/'
STORAGES = {{
    'default': {{'BACKEND': 'django.core.files.storage.FileSystemStorage'}},
    'staticfiles': {{'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
}}
DEBUG = False
OPENAI_API_KEY = ''
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fornec'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fornec-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_fornec_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from decimal import Decimal

from django.contrib.auth import get_user_model

from suppliers.models import Supplier

User = get_user_model()
OK = FALHAS = 0


def t(nome, cond, extra=''):
    global OK, FALHAS
    if cond:
        OK += 1
        print('  OK  ', nome)
    else:
        FALHAS += 1
        print('  FALHA', nome, extra)


chefe = User.objects.create_user(username='zz.forn', email='zz.forn@exemplo-teste.local', password='x', is_superuser=True,
                                 first_name='ZZ', last_name='Forn')
c = Client(); c.force_login(chefe)
base = {'name': 'ZZ Fornecedor', 'cnpj': '11.222.333/0001-81', 'contact': '(27) 99999-0000',
        'area_of_operation': 'ES', 'services': 'Impressão'}
h = c.get('/suppliers/novo/').content.decode()
t('cadastro tem os 3 campos, sem obrigatoriedade', all(f'name="{n}"' in h for n in ('preco', 'frete', 'tempo_entrega'))
  and 'name="preco" inputmode="decimal" placeholder="Ex.: 1.250,00"\n                               value=""' in h or 'id="preco" name="preco"' in h)
r = c.post('/suppliers/novo/', base, follow=True)
s1 = Supplier.objects.filter(name='ZZ Fornecedor').first()
t('sem os campos novos, cadastra normalmente', s1 is not None and s1.preco is None and s1.frete is None and s1.tempo_entrega == '')
r = c.post('/suppliers/novo/', dict(base, name='ZZ Com preço', cnpj='22.333.444/0001-81', preco='1.250,50', frete='R$ 80', tempo_entrega='5 dias úteis'), follow=True)
s2 = Supplier.objects.filter(name='ZZ Com preço').first()
t('grava preço (1.250,50), frete (R$ 80) e tempo de entrega', s2 and s2.preco == Decimal('1250.50') and s2.frete == Decimal('80.00')
  and s2.tempo_entrega == '5 dias úteis', (s2.preco, s2.frete) if s2 else None)
h = r.content.decode()
t('o detalhe mostra os três', 'R$ 1.250,50' in h and 'R$ 80,00' in h and '5 dias úteis' in h, h.count('R$'))
r = c.post('/suppliers/novo/', dict(base, name='ZZ Preço ruim', cnpj='33.444.555/0001-81', preco='abc'), follow=True)
t('preço inválido é recusado com aviso e o que foi digitado volta', not Supplier.objects.filter(name='ZZ Preço ruim').exists()
  and 'Valor inválido' in r.content.decode() and 'value="ZZ Preço ruim"' in r.content.decode())
h = c.get(f'/suppliers/{s2.pk}/editar/').content.decode() if c.get(f'/suppliers/{s2.pk}/editar/').status_code == 200 else ''
if not h:
    from django.urls import reverse
    h = c.get(reverse('suppliers:supplier_edit', args=[s2.pk])).content.decode()
t('edição vem preenchida (1.250,50 / 80,00)', 'value="1.250,50"' in h and 'value="80,00"' in h and 'value="5 dias úteis"' in h)
from django.urls import reverse
c.post(reverse('suppliers:supplier_edit', args=[s2.pk]), dict(base, name='ZZ Com preço', cnpj='22.333.444/0001-81', preco='', frete='99,9', tempo_entrega=''))
s2.refresh_from_db()
t('edição apaga o preço (vazio) e muda o frete', s2.preco is None and s2.frete == Decimal('99.90') and s2.tempo_entrega == '')

print('Descrição, categoria e preço sob consulta (25/11/2026)')
h = c.get('/suppliers/novo/').content.decode()
t('"O que faz" virou "Descrição"', 'Descrição *' in h and 'O que Faz' not in h)
t('categoria em select com "Criar nova" e campo escondido', 'id="f-categoria-sel"' in h and '+ Criar nova categoria' in h
  and 'name="categoria"' in h)
t('preço com "Valor fixo" e "Sob consulta"', 'value="fixo"' in h and 'value="consulta"' in h and 'name="preco_referencia"' in h)
c.post('/suppliers/novo/', dict(base, name='ZZ Elétrica', cnpj='44.555.666/0001-81', categoria='Manutenção predial',
                                preco_tipo='consulta', preco='999', preco_referencia='orçamento por visita'))
s3 = Supplier.objects.get(name='ZZ Elétrica')
t('sob consulta não grava valor e guarda o "o que consultar"', s3.preco_sob_consulta and s3.preco is None
  and s3.preco_referencia == 'orçamento por visita' and s3.categoria == 'Manutenção predial')
t('resumo do preço sob consulta', s3.preco_resumo == 'Sob consulta · orçamento por visita', s3.preco_resumo)
c.post('/suppliers/novo/', dict(base, name='ZZ Hidráulica', cnpj='55.666.777/0001-81', categoria='  manutencao   PREDIAL ',
                                preco_tipo='fixo', preco='150', preco_referencia='por visita técnica'))
s4 = Supplier.objects.get(name='ZZ Hidráulica')
t('categoria digitada com outra grafia é gravada com a de sempre', s4.categoria == 'Manutenção predial', s4.categoria)
t('valor fixo com referência', s4.preco == Decimal('150.00') and not s4.preco_sob_consulta
  and s4.preco_resumo == 'R$ 150,00 · por visita técnica', s4.preco_resumo)
h = c.get('/suppliers/novo/').content.decode()
t('a categoria nova aparece no select uma vez só', h.count('<option value="Manutenção predial">') == 1)
h = c.get('/suppliers/').content.decode()
t('lista mostra categoria, filtro e o preço resumido', 'Todas as categorias' in h and 'Sob consulta · orçamento por visita' in h
  and 'R$ 150,00 · por visita técnica' in h)
h = c.get('/suppliers/', {'categoria': 'manutenção predial'}).content.decode()
t('filtro por categoria', 'ZZ Elétrica' in h and 'ZZ Hidráulica' in h and 'ZZ Fornecedor<' not in h and '>ZZ Fornecedor\n' not in h
  and 'ZZ Com preço' not in h)
h = c.get(f'/suppliers/{s3.pk}/').content.decode()
t('detalhe mostra a categoria, "Sob consulta" e a seção Descrição', 'Manutenção predial' in h and 'Sob consulta' in h
  and 'orçamento por visita' in h and 'Serviços Oferecidos' not in h)
h = c.get(reverse('suppliers:supplier_edit', args=[s3.pk])).content.decode()
t('edição volta com "Sob consulta" marcado e a categoria', 'value="consulta" class="sr-only peer" checked' in h
  and 'id="f-categoria" value="Manutenção predial"' in h)
c.post(reverse('suppliers:supplier_edit', args=[s3.pk]), dict(base, name='ZZ Elétrica', cnpj='44.555.666/0001-81',
                                                            categoria='Elétrica', preco_tipo='fixo', preco='200,00'))
s3.refresh_from_db()
t('edição volta para valor fixo e troca a categoria', not s3.preco_sob_consulta and s3.preco == Decimal('200.00')
  and s3.categoria == 'Elétrica' and s3.preco_referencia == '')
r = c.post('/suppliers/novo/', dict(base, name='ZZ Erro', cnpj='123', categoria='Limpeza', preco_tipo='consulta',
                                    preco_referencia='por m²'))
h = r.content.decode()
t('erro no cadastro mantém categoria e "Sob consulta"', 'id="f-categoria" value="Limpeza"' in h
  and 'value="consulta" class="sr-only peer" checked' in h and 'value="por m²"' in h)

print(f'\n{OK} OK, {FALHAS} falha(s)')
