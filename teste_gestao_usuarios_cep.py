"""Gestão de usuários: o endereço vem do CEP, como no pré-cadastro.

Pedido (29/09/2026): "em /users/manage/users/create/ e
/users/manage/users/{id}/edit/, faça a busca pelo cep quando digitar da mesma
forma que funciona o pré cadastro, para preencher endereço".

O que este teste cobre:

- as duas telas trazem a busca (o mesmo `fetch` do pré-cadastro) e o aviso;
- rua, bairro, cidade e UF estão marcados para serem preenchidos — inclusive o
  UF, que aqui é um `<select>` e não um input;
- a busca é **um arquivo só**: o pré-cadastro passou a incluir o mesmo partial,
  então não há duas versões do comportamento;
- na gestão os campos **não** travam (quem cadastra pode corrigir à mão) e o
  CEP que não existe não bloqueia o envio — diferente do pré-cadastro, onde as
  duas coisas valem;
- o endpoint que a tela consulta responde certo para CEP achado, inexistente,
  malformado e serviço fora do ar.

O ViaCEP é dublado — nenhuma consulta sai para a internet.
"""
import json
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cep'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cep-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from users import cep as cep_svc
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


def tag_do_campo(html, name, elemento='input'):
    """A tag inteira do campo, para olhar os atributos dela."""
    i = html.index(f'name="{name}"')
    return html[html.rindex(f'<{elemento}', 0, i):html.index('>', i)]


ENDERECO = {'cep': '29100000', 'logradouro': 'Rua Sete de Setembro', 'bairro': 'Centro',
            'cidade': 'Vila Velha', 'uf': 'ES'}

marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='ZZ Loja do CEP')
    chefe = User.objects.create_user(
        username='zzcep.chefe', email='zzcep.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZCEP', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True,
        is_staff=True, sector=loja)
    alvo = User.objects.create_user(
        username='zzcep.alvo', email='zzcep.alvo@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZCEP', last_name='Alvo', sector=loja, cep='29100-000',
        address='Rua Sete de Setembro', neighborhood='Centro', city='Vila Velha', state='ES')

    c = Client()
    c.force_login(chefe)

    print('== AS DUAS TELAS ==')
    telas = {
        'criar': c.get('/users/manage/users/create/').content.decode(),
        'editar': c.get(f'/users/manage/users/{alvo.id}/edit/').content.decode(),
    }
    for rotulo, html in telas.items():
        t(f'{rotulo}: a tela abre com o campo de CEP', 'name="cep"' in html)
        t(f'{rotulo}: e consulta o mesmo endereço do pré-cadastro',
          "fetch('/users/cep/'" in html)
        t(f'{rotulo}: com o aviso do que vai acontecer',
          'cepAviso' in html and 'endereço vem preenchido' in html)
        t(f'{rotulo}: a rua é preenchida pela consulta',
          'data-cep-preenche="logradouro"' in tag_do_campo(html, 'address'),
          tag_do_campo(html, 'address')[:120])
        t(f'{rotulo}: o bairro também',
          'data-cep-preenche="bairro"' in tag_do_campo(html, 'neighborhood'))
        t(f'{rotulo}: a cidade também',
          'data-cep-preenche="cidade"' in tag_do_campo(html, 'city'))
        t(f'{rotulo}: e o UF, que aqui é um select',
          'data-cep-preenche="uf"' in tag_do_campo(html, 'state', 'select'),
          tag_do_campo(html, 'state', 'select')[:120])
        t(f'{rotulo}: o script sabe preencher select (o UF)', "el.tagName === 'SELECT'" in html)
        t(f'{rotulo}: a máscara do CEP continua', 'function mascara' in html)

    print('\n== O QUE MUDA ENTRE GESTÃO E PRÉ-CADASTRO ==')
    html = telas['criar']
    t('na gestão nada trava: quem cadastra corrige à mão',
      'readonly' not in tag_do_campo(html, 'address').lower())
    t('e um CEP recusado não impede de salvar', 'var EXIGIR = false' in html)
    t('a busca é o mesmo arquivo dos dois lados',
      'A busca do CEP mora em um lugar só' in
      open('templates/users/_pre_register_endereco.html').read())

    publico = Client()
    novo = User.objects.create_user(
        username='zzcep.novo', email='zzcep.novo@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZCEP', last_name='Novo', sector=loja)
    novo.pre_registration_status = User.PRE_REG_PENDING
    novo.pre_registration_token = 'zz-token-cep-gestao-0001'
    from django.utils import timezone
    novo.pre_registration_created_at = timezone.now()
    novo.save()
    pre = publico.get(f'/users/pre-cadastro/{novo.pre_registration_token}/').content.decode()
    t('no pré-cadastro os campos continuam travados',
      'readonly' in tag_do_campo(pre, 'address').lower())
    t('e o CEP inválido continua barrando o envio', 'var EXIGIR = true' in pre)

    print('\n== O ENDPOINT QUE A TELA CONSULTA ==')
    with mock.patch.object(cep_svc, 'buscar', return_value=ENDERECO):
        r = c.get('/users/cep/29100000/')
        dados = json.loads(r.content)
        t('CEP achado volta com o endereço',
          r.status_code == 200 and dados['situacao'] == 'ok'
          and dados['endereco']['cidade'] == 'Vila Velha', (r.status_code, dados))
    with mock.patch.object(cep_svc, 'buscar', return_value=None):
        r = c.get('/users/cep/29100001/')
        t('CEP que não existe responde 404', r.status_code == 404, r.status_code)
    with mock.patch.object(cep_svc, 'buscar', side_effect=cep_svc.CepIndisponivel('fora do ar')):
        r = c.get('/users/cep/29100000/')
        dados = json.loads(r.content)
        t('serviço fora do ar responde 503 e não culpa quem digitou',
          r.status_code == 503 and dados['situacao'] == 'indisponivel', (r.status_code, dados))
    r = c.get('/users/cep/123/')
    t('CEP malformado responde 400', r.status_code == 400, r.status_code)

    print('\n== SALVAR CONTINUA FUNCIONANDO ==')
    r = c.post(f'/users/manage/users/{alvo.id}/edit/', {
        'first_name': 'ZZCEP', 'last_name': 'Alvo', 'email': alvo.email,
        'username': alvo.username, 'hierarchy': 'PADRAO',
        'cep': '29100-000', 'address': 'Rua Sete de Setembro', 'address_number': '100',
        'neighborhood': 'Centro', 'city': 'Vila Velha', 'state': 'ES',
    }, follow=True)
    alvo.refresh_from_db()
    t('o endereço é gravado como veio da tela',
      alvo.city == 'Vila Velha' and alvo.state == 'ES' and alvo.address_number == '100',
      (alvo.city, alvo.state, alvo.address_number))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado; nenhuma consulta saiu para a internet.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
