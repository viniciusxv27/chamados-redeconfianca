"""Pré-cadastro: o endereço vem do CEP, e CEP errado volta para a pessoa.

Pedido: "preciso que o usuário só consiga preencher o CEP e o número da casa,
puxando endereço automaticamente, e quando o CEP não retornar nada ou for
inválido, deve informar que o CEP é inválido e que deve preencher novamente".

O que este teste cobre:

- a consulta (users/cep.py): as duas fontes, o cache, "não existe" diferente de
  "ninguém respondeu";
- o endpoint público /users/cep/<cep>/ nas três situações;
- o servidor sobrescrevendo o endereço pelo CEP — trava de HTML não vale como
  validação — e recusando CEP inválido ou inexistente;
- a queda do serviço não travando a admissão;
- a tela: só CEP, número e complemento editáveis; o resto readonly.

Nenhuma consulta sai para a internet: as fontes são dublês. Transação desfeita.
"""
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
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cep-local'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client, RequestFactory
from django.utils import timezone

from users import cep as cep_svc
from users.models import Sector
from users.views import _read_pre_registration_personal_data

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


SE = {'cep': '01001000', 'logradouro': 'Praça da Sé', 'bairro': 'Sé',
      'cidade': 'São Paulo', 'uf': 'SP'}

FICHA = {
    'full_name': 'ZZCep Colaborador', 'cpf': '390.533.447-05',
    'pis': '12345678901', 'phone': '(27) 99999-0000',
    'birth_date': '1995-05-05',
    'uniform_size_shirt': 'M', 'uniform_size_pants': 'M',
    'rg': '9876543', 'rg_issue_date': '2010-02-02', 'rg_issuer': 'SSP-ES',
    'father_name': 'Pai Teste', 'mother_name': 'Mae Teste',
    'skin_color': 'PARDA', 'sex': 'MASCULINO', 'gender': 'HOMEM',
    'marital_status': 'SOLTEIRO', 'birthplace': 'Vitoria',
    'nationality': 'Brasileira', 'education_level': 'MEDIO_COMPLETO',
    'emergency_name_1': 'Contato Teste', 'emergency_phone_1': '(27) 98888-0000',
    'emergency_relationship_1': 'Irmão', 'has_dependents': 'nao',
    # o que a tela mandaria nos campos travados
    'address': 'Rua Digitada à Mão', 'address_number': '10', 'address_complement': '',
    'neighborhood': 'Bairro Digitado', 'city': 'Cidade Digitada', 'state': 'ES',
}


def ler(**extra):
    dados = dict(FICHA)
    dados.update(extra)
    return _read_pre_registration_personal_data(
        RequestFactory().post('/users/pre-cadastro/zz/', dados))


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== O QUE É UM CEP ==')
    t('tira o que não é dígito', cep_svc.so_digitos('01001-000') == '01001000')
    t('formata para a tela', cep_svc.formatar('01001000') == '01001-000')
    t('oito dígitos é válido', cep_svc.valido('01001-000'))
    t('menos que isso, não', not cep_svc.valido('0100100'))
    t('nem tudo igual (00000000 é o "não sei" de quem preenche)',
      not cep_svc.valido('00000000') and not cep_svc.valido('99999999'))

    print('\n== A CONSULTA ==')
    # `buscar` recusa a internet em processo de teste; aqui o dublê é a fonte.
    sem_trava = mock.patch('core.utils.processo_de_teste', lambda: False)

    chamadas = []

    def viacep_ok(digitos):
        chamadas.append(('viacep', digitos))
        return dict(SE, cep=digitos)

    def viacep_vazio(digitos):
        chamadas.append(('viacep', digitos))
        return None

    def viacep_quebrado(digitos):
        chamadas.append(('viacep', digitos))
        raise RuntimeError('timeout')

    def brasilapi_ok(digitos):
        chamadas.append(('brasilapi', digitos))
        return dict(SE, cep=digitos, logradouro='Rua da Reserva')

    def brasilapi_quebrado(digitos):
        chamadas.append(('brasilapi', digitos))
        raise RuntimeError('500')

    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_ok),
                                                          ('BrasilAPI', brasilapi_ok))):
        achado = cep_svc.buscar('01001-000')
        t('acha e devolve rua, bairro, cidade e UF',
          (achado['logradouro'], achado['bairro'], achado['cidade'], achado['uf'])
          == ('Praça da Sé', 'Sé', 'São Paulo', 'SP'), achado)
        t('não vai na segunda fonte quando a primeira responde',
          [f for f, _ in chamadas] == ['viacep'], chamadas)
        chamadas.clear()
        cep_svc.buscar('01001-000')
        t('e a segunda consulta sai do cache (CEP não muda)', chamadas == [], chamadas)

    chamadas.clear()
    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_vazio),
                                                          ('BrasilAPI', brasilapi_ok))):
        t('CEP que não existe devolve None', cep_svc.buscar('20000111') is None)
        t('e nem pergunta para a reserva (a primeira já sabe)',
          [f for f, _ in chamadas] == ['viacep'], chamadas)

    chamadas.clear()
    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_quebrado),
                                                          ('BrasilAPI', brasilapi_ok))):
        achado = cep_svc.buscar('30110000')
        t('com a primeira fonte fora do ar, a reserva responde',
          achado and achado['logradouro'] == 'Rua da Reserva', achado)

    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_quebrado),
                                                          ('BrasilAPI', brasilapi_quebrado))):
        caiu = False
        try:
            cep_svc.buscar('40010000')
        except cep_svc.CepIndisponivel:
            caiu = True
        t('as duas fora do ar viram "indisponível", e não "CEP errado"', caiu)

    caiu = False
    try:
        cep_svc.buscar('50010000')          # sem o patch: processo de teste
    except cep_svc.CepIndisponivel:
        caiu = True
    t('em teste, a consulta não sai para a internet', caiu)

    print('\n== O ENDPOINT DA TELA ==')
    publico = Client()
    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_ok),)):
        r = publico.get('/users/cep/60110000/')
        t('CEP achado responde 200 com o endereço',
          r.status_code == 200 and r.json()['situacao'] == 'ok'
          and r.json()['endereco']['cidade'] == 'São Paulo', r.content[:120])
    r = publico.get('/users/cep/123/')
    t('CEP malformado responde 400 e manda digitar de novo',
      r.status_code == 400 and r.json()['situacao'] == 'invalido'
      and 'digite de novo' in r.json()['mensagem'], r.content[:120])
    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_vazio),)):
        r = publico.get('/users/cep/70002900/')
        t('CEP inexistente responde 404 com a mesma orientação',
          r.status_code == 404 and r.json()['situacao'] == 'invalido'
          and 'digite de novo' in r.json()['mensagem'], r.content[:120])
    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_quebrado),)):
        r = publico.get('/users/cep/80010000/')
        t('serviço fora do ar responde 503 (não é culpa de quem digitou)',
          r.status_code == 503 and r.json()['situacao'] == 'indisponivel', r.content[:120])

    print('\n== O SERVIDOR NÃO ACREDITA NO CAMPO TRAVADO ==')
    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_ok),)):
        valores, erros = ler(cep='90010-000')
        t('a ficha passa', not erros, erros)
        t('e o endereço é o do CEP, não o que veio digitado',
          (valores['address'], valores['neighborhood'], valores['city'], valores['state'])
          == ('Praça da Sé', 'Sé', 'São Paulo', 'SP'), valores['address'])
        t('o número e o complemento continuam sendo de quem preenche',
          valores['address_number'] == '10')
        t('o CEP é gravado com máscara, como no resto do cadastro',
          valores['cep'] == '90010-000', valores['cep'])

    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_vazio),)):
        valores, erros = ler(cep='20000-222')
        t('CEP inexistente barra o envio com a mensagem certa',
          'CEP não encontrado. Confira e digite de novo.' in erros, erros)
    valores, erros = ler(cep='123')
    t('CEP malformado também', 'CEP inválido. Confira e digite de novo.' in erros, erros)
    valores, erros = ler(cep='')
    t('e sem CEP a ficha nem sai', 'Informe o CEP.' in erros, erros)

    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_quebrado),
                                                          ('BrasilAPI', brasilapi_quebrado))):
        valores, erros = ler(cep='11010-000')
        t('serviço fora do ar não trava a admissão', not erros, erros)
        t('e o que estava na tela é mantido',
          valores['address'] == 'Rua Digitada à Mão' and valores['city'] == 'Cidade Digitada')

    def viacep_sem_rua(digitos):
        return dict(SE, cep=digitos, logradouro='', bairro='')

    with sem_trava, mock.patch.object(cep_svc, 'FONTES', (('ViaCEP', viacep_sem_rua),)):
        valores, erros = ler(cep='12940-000')
        t('"CEP geral" (sem rua na base) mantém a rua digitada', not erros
          and valores['address'] == 'Rua Digitada à Mão', (erros, valores['address']))
        t('mas cidade e UF continuam vindo da consulta',
          (valores['city'], valores['state']) == ('São Paulo', 'SP'))

    print('\n== A TELA ==')
    area = Sector.objects.create(name='ZZ Area CEP')
    novo = User.objects.create_user(
        username='zzcep.novo', email='zzcep.novo@exemplo-teste.local',
        sector=area, first_name='ZZCep', last_name='Novo', is_active=False)
    novo.set_unusable_password()
    novo.pre_registration_status = User.PRE_REG_PENDING
    novo.pre_registration_token = 'zz-token-de-teste-cep-0001'
    novo.pre_registration_created_at = timezone.now()
    novo.save()

    html = publico.get(f'/users/pre-cadastro/{novo.pre_registration_token}/').content.decode()
    t('o CEP está lá, com a explicação', 'name="cep"' in html
      and 'O endereço é preenchido pelo CEP' in html)
    def travado(campo):
        """O campo está marcado como somente leitura na própria tag?"""
        i = html.index(f'name="{campo}"')
        tag = html[html.rindex('<input', 0, i):html.index('>', i)]
        return 'readonly' in tag

    for campo in ('address', 'neighborhood', 'city', 'state'):
        t(f'{campo} vai travado na tela', travado(campo))
    for campo in ('cep', 'address_number', 'address_complement'):
        t(f'{campo} continua editável', not travado(campo))
    t('o estado deixou de ser um select', '<select name="state"' not in html)
    t('e a tela sabe consultar o CEP', "fetch('/users/cep/'" in html)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nenhuma consulta saiu.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
