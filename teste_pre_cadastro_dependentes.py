"""Pré-cadastro: o campo "Possui dependentes" e a lista de nome + documento.

Pedido: "em /users/pre-cadastro/{id} — coloque o campo 'Possui dependentes',
se tiver, coloque para adicionar o nome e documento (quantos tiver)".

O que este teste cobre:

- a pergunta é obrigatória e aceita só sim/não;
- "sim" sem nenhum dependente, ou linha com só um dos dois campos, é erro —
  não silêncio (a pessoa acha que cadastrou o filho e o RH fica sem o
  documento dele);
- "não" grava a resposta e limpa a lista; "sim" grava quantos vierem;
- o formulário devolve o que já está salvo (fluxo de ajuste) e o que foi
  digitado quando a validação falha;
- a tela pública, a ficha cadastral e o perfil do colaborador mostram o campo;
- INSERT antigo de usuário (sem a coluna nova) continua funcionando.

Caches em memória e transação desfeita no fim: nada é gravado, nada sai.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dep'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dep-local'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client, RequestFactory
from django.utils import timezone

from users.models import Dependent, Sector
from users.views import (_apply_pre_registration_personal_data, _dependent_rows,
                         _read_pre_registration_personal_data)

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


FICHA = {
    'full_name': 'ZZDep Colaborador Novo', 'cpf': '390.533.447-05',
    'pis': '12345678901', 'phone': '(27) 99999-0000',
    'birth_date': '1995-05-05', 'neighborhood': 'Centro',
    'city': 'Viana', 'cep': '29135-000',
    'uniform_size_shirt': 'M', 'uniform_size_pants': 'M',
    'rg': '9876543', 'rg_issue_date': '2010-02-02', 'rg_issuer': 'SSP-ES',
    'father_name': 'Pai Teste', 'mother_name': 'Mae Teste',
    'skin_color': 'PARDA', 'sex': 'MASCULINO', 'gender': 'HOMEM',
    'marital_status': 'SOLTEIRO', 'birthplace': 'Vitoria',
    'nationality': 'Brasileira', 'education_level': 'MEDIO_COMPLETO',
    'address': 'Rua Teste', 'address_number': '10', 'state': 'ES',
    'emergency_name_1': 'Contato Teste',
    'emergency_phone_1': '(27) 98888-0000',
    'emergency_relationship_1': 'Irmão',
}


def ficha(**extra):
    dados = dict(FICHA)
    dados.update(extra)
    return dados


def ler(**extra):
    return _read_pre_registration_personal_data(
        RequestFactory().post('/users/pre-cadastro/zz/', ficha(**extra)))


marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not User.objects.filter(username__startswith='zzdep.').exists(), 'usuários do teste já existem'
    area = Sector.objects.create(name='ZZ Area Dependentes')

    novo = User.objects.create_user(
        username='zzdep.novo', email='zzdep.novo@exemplo-teste.local',
        sector=area, first_name='ZZDep', last_name='Novo', is_active=False)
    novo.set_unusable_password()
    novo.pre_registration_status = User.PRE_REG_PENDING
    novo.pre_registration_token = 'zz-token-de-teste-dependentes-1'
    novo.pre_registration_created_at = timezone.now()
    novo.save()

    print('== A PERGUNTA É OBRIGATÓRIA ==')
    valores, erros = ler()
    t('sem responder, não passa', 'Informe se você possui dependentes.' in erros, erros)
    valores, erros = ler(has_dependents='talvez')
    t('resposta fora de sim/não também não passa',
      'Informe se você possui dependentes.' in erros, erros)
    valores, erros = ler(has_dependents='nao')
    t('"não" é resposta válida e o resto da ficha está ok', not erros, erros)
    t('e a leitura guarda o "não"', valores['has_dependents'] is False)

    print('\n== "SIM" PRECISA DE NOME E DOCUMENTO ==')
    valores, erros = ler(has_dependents='sim')
    t('sim sem ninguém é erro',
      'Informe o nome e o documento de cada dependente.' in erros, erros)
    valores, erros = ler(has_dependents='sim', dependent_name_1='ZZDep Filho')
    t('só o nome é erro', 'Cada dependente precisa de nome E documento.' in erros, erros)
    valores, erros = ler(has_dependents='sim', dependent_document_1='111.222.333-44')
    t('só o documento também', 'Cada dependente precisa de nome E documento.' in erros, erros)
    valores, erros = ler(has_dependents='sim',
                         dependent_name_1='ZZDep Filho', dependent_document_1='111.222.333-44',
                         dependent_name_2='ZZDep Filha', dependent_document_2='555.666.777-88')
    t('dois dependentes completos passam', not erros, erros)
    t('e a leitura traz os dois, na ordem',
      [d['name'] for d in valores['dependents']] == ['ZZDep Filho', 'ZZDep Filha'], valores['dependents'])
    t('com o documento junto', valores['dependents'][1]['document'] == '555.666.777-88')

    print('\n== GRAVAÇÃO ==')
    _apply_pre_registration_personal_data(novo, valores)
    novo.save()
    novo.refresh_from_db()
    t('grava a resposta', novo.has_dependents)
    t('e a lista inteira',
      [(d.name, d.document) for d in novo.dependents.all()]
      == [('ZZDep Filho', '111.222.333-44'), ('ZZDep Filha', '555.666.777-88')])

    # Reenvio com um a menos: a lista do formulário substitui a anterior.
    valores, _ = ler(has_dependents='sim', dependent_name_1='ZZDep Filho',
                     dependent_document_1='111.222.333-44')
    _apply_pre_registration_personal_data(novo, valores)
    novo.save(); novo.refresh_from_db()
    t('reenviar com um a menos remove o que saiu', novo.dependents.count() == 1)

    valores, _ = ler(has_dependents='nao', dependent_name_1='ZZDep Filho',
                     dependent_document_1='111.222.333-44')
    _apply_pre_registration_personal_data(novo, valores)
    novo.save(); novo.refresh_from_db()
    t('mudar para "não" limpa a lista',
      not novo.has_dependents and novo.dependents.count() == 0)

    print('\n== AS LINHAS DO FORMULÁRIO ==')
    Dependent.objects.create(user=novo, name='ZZDep Filho', document='111.222.333-44')
    linhas = _dependent_rows(RequestFactory().get('/x/'), novo)
    t('o GET devolve o que está salvo na primeira linha',
      linhas[0]['name'] == 'ZZDep Filho' and linhas[0]['document'] == '111.222.333-44')
    t('as demais vêm vazias e escondidas',
      not linhas[1]['preenchido'] and not linhas[1]['primeira'])
    t('cabem quantos ele tiver (até o limite do modelo)',
      len(linhas) == Dependent.MAX_POR_USUARIO and Dependent.MAX_POR_USUARIO >= 10)
    pedido = RequestFactory().post('/x/', ficha(has_dependents='sim',
                                                dependent_name_1='ZZDep Digitado',
                                                dependent_document_1='999'))
    linhas = _dependent_rows(pedido, novo)
    t('quando a validação falha, o POST manda na tela (nada se perde)',
      linhas[0]['name'] == 'ZZDep Digitado')

    print('\n== AS TELAS ==')
    publico = Client()
    html = publico.get(f'/users/pre-cadastro/{novo.pre_registration_token}/').content.decode()
    t('a tela pública pergunta', 'Possui dependentes' in html
      and 'name="has_dependents"' in html)
    t('e tem as linhas de nome e documento',
      'name="dependent_name_1"' in html and 'name="dependent_document_1"' in html)
    t('com o botão de adicionar mais', 'Adicionar dependente' in html)
    t('nenhum campo de dependente é obrigatório no HTML (o "Não" esconde o bloco)',
      'name="dependent_name_1" maxlength="150" value="" required' not in html
      and 'required' in html)

    gestor = User.objects.create_user(
        username='zzdep.gestor', email='zzdep.gestor@exemplo-teste.local',
        sector=area, first_name='ZZDep', last_name='Gestor',
        password='S3nha!teste', hierarchy='SUPERADMIN', is_staff=True, is_superuser=True)
    c = Client(); c.force_login(gestor)
    html = c.get(f'/users/manage/users/{novo.id}/profile/').content.decode()
    t('o perfil mostra os dependentes', 'Dependentes' in html and 'ZZDep Filho' in html)
    html = c.get(f'/users/manage/users/{novo.id}/profile/print/').content.decode()
    t('a ficha cadastral também', 'Dependentes' in html and 'ZZDep Filho' in html
      and 'Possui dependentes' in html)

    novo.dependents.all().delete()
    novo.has_dependents = False
    novo.save(update_fields=['has_dependents'])
    html = c.get(f'/users/manage/users/{novo.id}/profile/').content.decode()
    t('quem não tem dependente aparece como respondido', 'não possui dependentes' in html)

    print('\n== O BANCO COMPARTILHADO ==')
    # Outro servidor, com o código de antes desta migração, insere usuário sem
    # a coluna nova. users_user tem dezenas de colunas NOT NULL, então em vez
    # de remontar o INSERT inteiro a conferência é no catálogo: a coluna
    # precisa ter DEFAULT no banco (db_default), senão esse INSERT estoura.
    with connection.cursor() as cur:
        cur.execute("""
            SELECT column_default, is_nullable FROM information_schema.columns
            WHERE table_name = 'users_user' AND column_name = 'has_dependents'
        """)
        padrao, aceita_nulo = cur.fetchone()
    t('a coluna nova tem DEFAULT no banco (INSERT do código antigo não quebra)',
      padrao is not None and 'false' in padrao.lower(), padrao)
    t('e continua NOT NULL', aceita_nulo == 'NO', aceita_nulo)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
