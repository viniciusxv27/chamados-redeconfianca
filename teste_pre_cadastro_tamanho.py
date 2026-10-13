"""Pré-cadastro: valor maior que a coluna ou data em texto não pode virar erro 500.

Pedido (13/10/2026): "/users/pre-cadastro/<token>/ — ao tentar enviar ele dá
erro 500". Era o banco recusando no `save()` um valor que não cabia na coluna
(ex.: "42/44" no tamanho da calça, que aceita 3 letras) — a pessoa perdia o
que digitou e o contato de emergência ficava gravado com o cadastro pendente.

Cobre: aviso por campo longo demais, data dd/mm/aaaa aceita, data impossível
avisada, maxlength no formulário e envio válido concluindo. Arquivos vão para
um storage em memória (nada sobe no MinIO). Transação desfeita no fim.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-pct'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-pct2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client

import users.views as uv
from users.models import EmergencyContact, RequiredDocument, UserDocument

U = get_user_model()
memoria = InMemoryStorage()
UserDocument._meta.get_field('file').storage = memoria
U._meta.get_field('profile_picture').storage = memoria
uv._endereco_pelo_cep = lambda values, errors: None      # sem ida ao ViaCEP
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def formulario(**troca):
    dados = {'password': 'abc12345', 'password_confirm': 'abc12345', 'full_name': 'Zz Pré Cadastro',
             'cpf': '529.982.247-25', 'pis': '12345678901', 'phone': '27999990000', 'birth_date': '1995-05-10',
             'neighborhood': 'Centro', 'city': 'Vitória', 'cep': '29010-000', 'rg': '123', 'rg_issue_date': '2010-01-01',
             'rg_issuer': 'SSP', 'voter_title': '1', 'voter_zone': '1', 'voter_section': '1', 'father_name': 'Pai',
             'mother_name': 'Mãe', 'skin_color': U.SKIN_COLOR_CHOICES[0][0], 'sex': U.SEX_CHOICES[0][0],
             'gender': U.GENDER_CHOICES[0][0], 'marital_status': U.MARITAL_STATUS_CHOICES[0][0], 'birthplace': 'Vitória',
             'nationality': 'Brasileira', 'education_level': U.EDUCATION_LEVEL_CHOICES[0][0], 'address': 'Rua A',
             'address_number': '1', 'state': 'ES', 'has_dependents': 'nao', 'uniform_size_shirt': 'M',
             'uniform_size_pants': 'M', 'emergency_name_1': 'Contato', 'emergency_phone_1': '27999991111',
             'emergency_relationship_1': 'Mãe'}
    for d in RequiredDocument.objects.filter(is_active=True):
        dados[f'document_{d.id}'] = SimpleUploadedFile(f'doc{d.id}.jpg', b'\xff\xd8\xff' + b'x' * 100,
                                                       content_type='image/jpeg')
    dados.update(troca)
    return dados


marcador = transaction.atomic()
marcador.__enter__()
try:
    alvo = U.objects.create_user(username='zz.precad.tam', email='zz.precad.tam@exemplo-teste.local',
                                 password='x', first_name='Zz', is_active=False)
    alvo.pre_registration_status = U.PRE_REG_PENDING
    alvo.pre_registration_token = 'zzTokenDeTestePreCadastroTamanho0001'
    alvo.save()
    url = f'/users/pre-cadastro/{alvo.pre_registration_token}/'
    c = Client(raise_request_exception=False)

    print('== O QUE DAVA 500 ==')
    r = c.post(url, formulario(uniform_size_pants='42/44', voter_zone='0123456789012'))
    avisos = [str(m) for m in get_messages(r.wsgi_request)]
    t('calça "42/44" não derruba mais a página', r.status_code == 200, r.status_code)
    t('avisa o campo e o limite', any('Tamanho uniforme (Calça)' in a and '3 caracteres' in a for a in avisos), avisos)
    t('avisa todos os campos de uma vez', any('Zona Eleitoral' in a for a in avisos), avisos)
    alvo.refresh_from_db()
    t('nada fica gravado pela metade', alvo.pre_registration_status == U.PRE_REG_PENDING
      and not EmergencyContact.objects.filter(user=alvo).exists())
    t('o que foi digitado volta preenchido', 'value="42/44"' in r.content.decode())

    print('\n== DATAS ==')
    r = c.post(url, formulario(birth_date='31/02/1995'))
    avisos = [str(m) for m in get_messages(r.wsgi_request)]
    t('data impossível: aviso, não 500', r.status_code == 200 and any('31/02/1995' in a and 'não existe' in a for a in avisos), avisos)

    print('\n== FORMULÁRIO ==')
    html = c.get(url).content.decode()
    t('campos curtos limitados no próprio formulário', 'name="uniform_size_pants" maxlength="3"' in html
      and 'name="voter_zone" maxlength="10"' in html)

    print('\n== ENVIO VÁLIDO ==')
    r = c.post(url, formulario(birth_date='10/05/1995'))
    alvo.refresh_from_db()
    t('data dd/mm/aaaa aceita e cadastro concluído', r.status_code == 302 and alvo.pre_registration_status == U.PRE_REG_COMPLETED
      and str(alvo.birth_date) == '1995-05-10', (r.status_code, alvo.pre_registration_status, alvo.birth_date))
finally:
    marcador.__exit__(Exception, Exception('rollback'), None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
