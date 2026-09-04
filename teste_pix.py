"""Chave PIX: no perfil do usuário (/users/manage/users/) e no pré-cadastro.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from users.models import Sector, UserChangeLog
from users.pix import identificar

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


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== A CHAVE É NORMALIZADA, NÃO GUARDADA COMO VEIO ==')
    casos_bons = [
        ('123.456.789-09', '12345678909', 'CPF'),
        ('12345678909', '12345678909', 'CPF'),
        ('11.222.333/0001-81', '11222333000181', 'CNPJ'),
        ('(27) 99999-8888', '+5527999998888', 'Celular'),
        ('+55 27 99999-8888', '+5527999998888', 'Celular'),
        ('27999998888', '+5527999998888', 'Celular'),
        ('  Fulano@Exemplo.COM ', 'fulano@exemplo.com', 'E-mail'),
        ('123E4567-E89B-12D3-A456-426614174000',
         '123e4567-e89b-12d3-a456-426614174000', 'Chave aleatória'),
    ]
    for entrada, esperado, _rotulo in casos_bons:
        chave, _tipo = identificar(entrada)
        t(f'{entrada!r} vira {esperado!r}', chave == esperado, chave)

    print('\n== O QUE NÃO É CHAVE É RECUSADO ==')
    for ruim in ['chave qualquer', '1234', '11111111111', '99999999999',
                 '2733334444', '+1 415 555 2671', '123.456.789-00',
                 '11.222.333/0001-99', 'sem-arroba.com',
                 '123e4567-e89b-12d3-a456-42661417400',
                 'a' * 80 + '@exemplo.com', 'x' * 200]:
        chave, _ = identificar(ruim)
        t(f'{ruim!r} é recusado', chave == '', chave)

    area = Sector.objects.create(name='ZZ Area PIX')
    admin = User.objects.create_user(
        username='zz.pixadmin', email='zz.pixadmin@exemplo-teste.local',
        password='S3nha!teste', sector=area, first_name='Pix', last_name='Admin',
        hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    alvo = User.objects.create_user(
        username='zz.pixalvo', email='zz.pixalvo@exemplo-teste.local',
        password='S3nha!teste', sector=area, first_name='Pix', last_name='Alvo',
        cpf='39053344705', rg='1234567', cep='29100-000')

    c = Client()
    c.force_login(admin)

    print('\n== NO PERFIL (/users/manage/users/) ==')
    html = c.get(f'/users/manage/users/{alvo.id}/edit/').content.decode()
    t('o campo aparece no formulário', 'name="pix_key"' in html)
    t('com rótulo "Chave PIX"', 'Chave PIX' in html)

    def editar(**extra):
        dados = {
            'username': alvo.username, 'email': alvo.email,
            'first_name': alvo.first_name, 'last_name': alvo.last_name,
            'hierarchy': alvo.hierarchy, 'sector': str(area.id),
            'job_title': 'VENDEDOR', 'cpf': alvo.cpf, 'rg': alvo.rg,
            'cep': alvo.cep, 'birth_date': '1990-01-01', 'status': 'ATIVO',
            'is_active': 'on',
        }
        dados.update(extra)
        return c.post(f'/users/manage/users/{alvo.id}/edit/', dados, follow=True)

    editar(pix_key='(27) 99999-8888')
    alvo.refresh_from_db()
    t('salva normalizada', alvo.pix_key == '+5527999998888', alvo.pix_key)
    t('o tipo é deduzido', alvo.pix_key_type == 'Celular', alvo.pix_key_type)
    t('e exibida com máscara', alvo.pix_key_display == '(27) 99999-8888',
      alvo.pix_key_display)

    html = c.get(f'/users/manage/users/{alvo.id}/edit/').content.decode()
    t('a tela devolve a chave com máscara', 'value="(27) 99999-8888"' in html)
    t('e mostra o tipo', 'Tipo atual: Celular' in html)

    r = editar(pix_key='isso não é chave')
    alvo.refresh_from_db()
    t('chave inválida não é salva', alvo.pix_key == '+5527999998888', alvo.pix_key)
    t('e a chave anterior não é apagada', alvo.pix_key != '')
    t('a tela explica o erro', 'Chave PIX inválida' in r.content.decode())

    editar(pix_key='')
    alvo.refresh_from_db()
    t('em branco limpa (o campo é opcional)', alvo.pix_key == '', alvo.pix_key)

    print('\n== A MUDANÇA FICA NO HISTÓRICO ==')
    editar(pix_key='fulano@exemplo.com')
    alvo.refresh_from_db()
    t('gravou o e-mail', alvo.pix_key == 'fulano@exemplo.com', alvo.pix_key)
    log = UserChangeLog.objects.filter(target=alvo, field='pix_key').first()
    t('a auditoria registrou', log is not None)
    t('com rótulo legível', log and log.field_label == 'Chave PIX',
      log and log.field_label)

    print('\n== NA FICHA E NO PERFIL SÓ LEITURA ==')
    perfil = c.get(f'/users/manage/users/{alvo.id}/profile/')
    t('o perfil abre', perfil.status_code == 200, perfil.status_code)
    html = perfil.content.decode()
    t('perfil mostra a chave', 'fulano@exemplo.com' in html)
    t('perfil mostra o tipo', 'Tipo da chave PIX' in html and 'E-mail' in html)

    ficha = c.get(f'/users/manage/users/{alvo.id}/profile/print/')
    t('a ficha impressa abre', ficha.status_code == 200, ficha.status_code)
    corpo = ficha.content.decode()
    t('a ficha impressa traz a chave', 'fulano@exemplo.com' in corpo)
    t('a ficha diz o tipo', '(E-mail)' in corpo)

    print('\n== NO PRÉ-CADASTRO ==')
    novo = User.objects.create_user(
        username='zz.pixnovo', email='zz.pixnovo@exemplo-teste.local',
        sector=area, first_name='Pix', last_name='Novo', is_active=False)
    novo.set_unusable_password()
    novo.pre_registration_status = User.PRE_REG_PENDING
    novo.pre_registration_token = 'zz-token-de-teste-pix-0001'
    novo.pre_registration_created_at = timezone.now()
    novo.save()

    publico = Client()
    url = f'/users/pre-cadastro/{novo.pre_registration_token}/'
    html = publico.get(url).content.decode()
    t('o campo aparece para o colaborador', 'name="pix_key"' in html)
    t('marcado como opcional', 'Chave PIX' in html and '(opcional)' in html)

    from users.models import RequiredDocument
    obrigatorios = list(RequiredDocument.objects.filter(is_active=True, is_required=True))

    def preencher(pix):
        return {
            'full_name': 'Pix Novo Colaborador', 'cpf': '390.533.447-05',
            'pis': '12345678901', 'phone': '(27) 99999-0000',
            'birth_date': '1995-05-05', 'neighborhood': 'Centro',
            'city': 'Viana', 'cep': '29135-000',
            'uniform_size_shirt': 'M', 'uniform_size_pants': 'M',
            'rg': '9876543', 'rg_issue_date': '2010-02-02', 'rg_issuer': 'SSP-ES',
            'voter_title': '', 'voter_zone': '', 'voter_section': '',
            'father_name': 'Pai Teste', 'mother_name': 'Mae Teste',
            'skin_color': 'PARDA', 'sex': 'MASCULINO', 'gender': 'HOMEM',
            'marital_status': 'SOLTEIRO', 'birthplace': 'Vitoria',
            'nationality': 'Brasileira', 'education_level': 'MEDIO_COMPLETO',
            'address': 'Rua Teste', 'address_number': '10',
            'address_complement': '', 'state': 'ES',
            'emergency_name_1': 'Contato Teste',
            'emergency_phone_1': '(27) 98888-0000',
            'emergency_relationship_1': 'Irmão',
            'password': 'S3nha!teste', 'password_confirm': 'S3nha!teste',
            'pix_key': pix,
        }

    r = publico.post(url, preencher('chave inventada'), follow=True)
    novo.refresh_from_db()
    t('chave inválida barra o envio',
      novo.pre_registration_status == User.PRE_REG_PENDING)
    t('e a tela explica', 'Chave PIX inválida' in r.content.decode())

    # O POST completo exigiria anexar os documentos obrigatórios (e escrever no
    # MinIO). O que importa aqui é a leitura/gravação dos dados, então exercita
    # o mesmo par de funções que a view usa, sem tocar em storage.
    from django.test import RequestFactory

    from users.views import (_apply_pre_registration_personal_data,
                             _read_pre_registration_personal_data)

    pedido = RequestFactory().post(url, preencher('390.533.447-05'))
    valores, erros = _read_pre_registration_personal_data(pedido)
    t('formulário válido não gera erro', not erros, erros)
    t('a chave sai normalizada da leitura', valores['pix_key'] == '39053344705',
      valores['pix_key'])
    _apply_pre_registration_personal_data(novo, valores)
    novo.save()
    novo.refresh_from_db()
    t('a chave do colaborador foi gravada', novo.pix_key == '39053344705',
      novo.pix_key)
    t('e o RH vê que é CPF', novo.pix_key_type == 'CPF', novo.pix_key_type)

    pedido = RequestFactory().post(url, preencher(''))
    valores, erros = _read_pre_registration_personal_data(pedido)
    t('deixar em branco não é erro', not erros, erros)

    # O formulário devolve a chave já gravada quando o colaborador reabre o
    # link (fluxo de ajuste), com máscara — e a máscara volta a ser aceita.
    t('a tela reexibe com máscara', novo.pix_key_display == '390.533.447-05',
      novo.pix_key_display)
    t('e a máscara é aceita de volta',
      identificar(novo.pix_key_display)[0] == '39053344705')

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
