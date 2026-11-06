import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: a tabela das entregas e a coluna
# `exige_entrega` só existem no Postgres compartilhado depois do migrate, e o
# migrate é dele. O schema aqui sai direto dos models (`--run-syncdb`), como em
# qualquer outra sessão — nada toca o banco de verdade nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-usredit-'))
(PASTA / 'zz_usredit_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_usredit_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from contextlib import ExitStack
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

from django.core import mail
from django.db import transaction
from django.test.utils import override_settings

from users.models import Sector, UserChangeLog, EmergencyContact, Dependent

User = get_user_model()
ok = fail = 0

ALVOS_DE_ENVIO = [
    'core.zapi.send_whatsapp_message',
    'core.evolution.enviar_texto',
    'users.resend_email.enviar',
    'notifications.push_utils.send_push_notification_to_user',
    'notifications.push_utils.send_push_notification_to_users',
    'notifications.models.PushNotification.send_notification',
    'urllib.request.urlopen',
    'requests.Session.request',
]


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


class Formulario(HTMLParser):
    """Lê o <form method="post"> da tela como o navegador enviaria."""

    def __init__(self):
        super().__init__()
        self.dados = []          # (nome, valor)
        self.dentro = False
        self.select = None       # [nome, primeira, escolhida]
        self.textarea = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form' and (a.get('method') or '').lower() == 'post' and 'enctype' in a:
            self.dentro = True
        if not self.dentro:
            return
        nome = a.get('name')
        if tag == 'input' and nome:
            tipo = (a.get('type') or 'text').lower()
            if tipo == 'file':
                return
            if tipo in ('checkbox', 'radio'):
                if 'checked' in a:
                    self.dados.append((nome, a.get('value', 'on')))
                return
            self.dados.append((nome, a.get('value', '')))
        elif tag == 'select' and nome:
            self.select = [nome, None, None]
        elif tag == 'option' and self.select is not None:
            valor = a.get('value', '')
            if self.select[1] is None:
                self.select[1] = valor
            if 'selected' in a:
                self.select[2] = valor
        elif tag == 'textarea' and nome:
            self.textarea = [nome, '']

    def handle_data(self, data):
        if self.textarea is not None:
            self.textarea[1] += data

    def handle_endtag(self, tag):
        if tag == 'form':
            self.dentro = False
        elif tag == 'select' and self.select is not None:
            nome, primeira, escolhida = self.select
            self.dados.append((nome, escolhida if escolhida is not None else (primeira or '')))
            self.select = None
        elif tag == 'textarea' and self.textarea is not None:
            self.dados.append(tuple(self.textarea))
            self.textarea = None


def form_da_tela(html):
    p = Formulario()
    p.feed(html)
    dados = {}
    for nome, valor in p.dados:
        dados.setdefault(nome, []).append(valor)
    return dados


def trocar(dados, **novos):
    d = {k: list(v) for k, v in dados.items()}
    for k, v in novos.items():
        d[k] = v if isinstance(v, list) else [v]
    return d


NOVOS = ('rg', 'rg_issue_date', 'rg_issuer', 'voter_title', 'voter_zone', 'voter_section',
         'father_name', 'mother_name', 'skin_color', 'sex', 'gender', 'marital_status',
         'birthplace', 'nationality', 'education_level', 'has_dependents', 'pcn',
         'tangerino_employee_id', 'theme', 'popup_janela_experiencia',
         'emergency_name_1', 'emergency_phone_1', 'emergency_relationship_1',
         'dependent_name_1', 'dependent_document_1', 'dependent_birth_1')

# Campos que já existiam na tela: não podem mudar só por salvar.
ANTIGOS = ('first_name', 'last_name', 'email', 'username', 'phone', 'disc_profile',
           'uniform_size_shirt', 'uniform_size_pants', 'cpf', 'pis', 'job_title',
           'contract_type', 'branch_cnpj', 'salary', 'pix_key', 'birth_date',
           'admission_date', 'demission_date', 'has_experience_window', 'login_code', 'pdv',
           'cep', 'address', 'address_number', 'address_complement', 'neighborhood', 'city',
           'state', 'sector_id', 'hierarchy', 'is_active', 'status')

marcador = transaction.atomic()
marcador.__enter__()
pilha = ExitStack()
try:
    for alvo in ALVOS_DE_ENVIO:
        try:
            pilha.enter_context(mock.patch(alvo))
        except (AttributeError, ModuleNotFoundError):
            pass
    import tangerino.middleware as middleware_tangerino
    if hasattr(middleware_tangerino, 'disparar_se_esta_na_hora'):
        pilha.enter_context(mock.patch.object(middleware_tangerino, 'disparar_se_esta_na_hora', return_value=False))
    pilha.enter_context(override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'))
    mail.outbox = []

    loja = Sector.objects.create(name='ZZ Loja Edição')
    outra = Sector.objects.create(name='ZZ Loja Dois')
    admin = User.objects.create_user(
        username='zz.edadmin', email='zz.edadmin@exemplo-teste.local', password='S3nha!teste',
        sector=loja, first_name='Ed', last_name='Admin', hierarchy='SUPERADMIN',
        is_superuser=True, is_staff=True)
    alvo = User.objects.create_user(
        username='zz.edalvo', email='zz.edalvo@exemplo-teste.local', password='S3nha!teste',
        first_name='Fulano', last_name='de Tal Silva', sector=loja, hierarchy='PADRAO',
        phone='(27) 99999-0000', disc_profile='DI', uniform_size_shirt='M', uniform_size_pants='40',
        cpf='39053344705', pis='12345678901', job_title='VENDEDOR', contract_type='CLT',
        branch_cnpj='12.345.678/0001-90', salary=Decimal('3500.00'), pix_key='39053344705',
        birth_date=date(1990, 5, 10), admission_date=date(2025, 1, 6), demission_date=None,
        has_experience_window=False, login_code='L123', pdv='PDV9', cep='29100-000',
        address='Rua A', address_number='12', address_complement='Apto 1', neighborhood='Centro',
        city='Vila Velha', state='ES', status='ATIVO', balance_cs=Decimal('77.50'),
        rg='111', rg_issuer='SSP/ES', father_name='Pai Antigo', has_dependents=True)
    alvo.sectors.set([loja, outra])
    EmergencyContact.objects.create(user=alvo, name='Mãe', phone='(27) 98888-0000', relationship='mãe')
    Dependent.objects.create(user=alvo, name='Filho Antigo', document='CERT-1', birth_date=None)
    outro = User.objects.create_user(username='zz.edoutro', email='zz.edoutro@exemplo-teste.local',
                                     password='x', tangerino_employee_id=555)
    antes = {c: getattr(alvo, c) for c in ANTIGOS}

    c = Client()
    c.force_login(admin)
    url = f'/users/manage/users/{alvo.id}/edit/'

    print('== TELA ==')
    r = c.get(url)
    html = r.content.decode()
    t('abre (200)', r.status_code == 200, r.status_code)
    for nome in NOVOS:
        t(f'campo {nome} na tela', f'name="{nome}"' in html)
    t('saldo C$ só para leitura', 'C$ 77,50' in html and 'name="balance_cs"' not in html)
    for proibido in ('password', 'is_superuser', 'is_staff', 'balance_cs', 'pre_registration_token',
                     'last_login', 'date_joined', 'menu_favoritos'):
        t(f'{proibido} não é editável', f'name="{proibido}"' not in html)
    t('comentário Django não vazou', '{#' not in html and '#}' not in html)
    t('contato e dependente antigos vêm preenchidos',
      'value="Mãe"' in html and 'value="Filho Antigo"' in html)

    print('\n== SALVAR SEM MEXER (formulário como o navegador manda) ==')
    form = form_da_tela(html)
    t('o parser leu o formulário', 'full_name' in form and 'rg' in form, sorted(form)[:10])
    r = c.post(url, form)
    t('redireciona', r.status_code == 302, r.status_code)
    alvo.refresh_from_db()
    mudou = {k: (v, getattr(alvo, k)) for k, v in antes.items() if getattr(alvo, k) != v}
    t('nenhum campo antigo mudou', not mudou, mudou)
    t('rg / pai mantidos', alvo.rg == '111' and alvo.father_name == 'Pai Antigo' and alvo.rg_issuer == 'SSP/ES')
    t('setores mantidos', set(alvo.sectors.all()) == {loja, outra})
    t('saldo C$ intacto', alvo.balance_cs == Decimal('77.50'))
    t('contato mantido', list(alvo.emergency_contacts.values_list('name', flat=True)) == ['Mãe'])
    t('dependente antigo sem nascimento mantido',
      list(alvo.dependents.values_list('name', 'birth_date')) == [('Filho Antigo', None)])
    t('popup segue ligado', alvo.popup_janela_experiencia is True)
    t('nada no histórico', not UserChangeLog.objects.filter(target=alvo).exists(),
      list(UserChangeLog.objects.filter(target=alvo).values_list('field', 'old_value', 'new_value')))

    print('\n== SALVAR OS CAMPOS NOVOS ==')
    novo = trocar(form, rg='MG-12.345', rg_issue_date='2010-03-04', rg_issuer='SSP/MG',
                  voter_title='123456789012', voter_zone='045', voter_section='0123',
                  father_name='José Pai', mother_name='Maria Mãe', skin_color='PARDA', sex='FEMININO',
                  gender='MULHER', marital_status='CASADO', birthplace='Vitória/ES',
                  nationality='Brasileira', education_level='SUPERIOR_COMPLETO', pcn='85%',
                  tangerino_employee_id='777', theme='dark', has_dependents='sim',
                  emergency_name_2='Irmão', emergency_phone_2='(27) 97777-0000',
                  emergency_relationship_2='irmão',
                  dependent_name_2='Filha Nova', dependent_document_2='123.456.789-09',
                  dependent_birth_2='15/08/2018')
    novo.pop('popup_janela_experiencia', None)       # desmarcar a caixa
    r = c.post(url, novo)
    t('redireciona', r.status_code == 302, r.status_code)
    alvo.refresh_from_db()
    esperado = dict(rg='MG-12.345', rg_issue_date=date(2010, 3, 4), rg_issuer='SSP/MG',
                    voter_title='123456789012', voter_zone='045', voter_section='0123',
                    father_name='José Pai', mother_name='Maria Mãe', skin_color='PARDA', sex='FEMININO',
                    gender='MULHER', marital_status='CASADO', birthplace='Vitória/ES',
                    nationality='Brasileira', education_level='SUPERIOR_COMPLETO', pcn='85%',
                    tangerino_employee_id=777, theme='dark', has_dependents=True,
                    popup_janela_experiencia=False)
    for campo, valor in esperado.items():
        t(f'{campo} salvo', getattr(alvo, campo) == valor, (getattr(alvo, campo), valor))
    t('contatos: os dois', list(alvo.emergency_contacts.order_by('id').values_list('name', flat=True)) == ['Mãe', 'Irmão'])
    t('dependentes: antigo + nova (data dd/mm aceita)',
      list(alvo.dependents.order_by('id').values_list('name', 'birth_date')) ==
      [('Filho Antigo', None), ('Filha Nova', date(2018, 8, 15))])
    mudou = {k: (v, getattr(alvo, k)) for k, v in antes.items() if getattr(alvo, k) != v}
    t('campos antigos continuam iguais', not mudou, mudou)
    campos_log = set(UserChangeLog.objects.filter(target=alvo).values_list('field', flat=True))
    t('histórico registra os campos novos e as listas',
      {'rg', 'sex', 'tangerino_employee_id', 'popup_janela_experiencia', 'emergency_contacts', 'dependents'} <= campos_log,
      campos_log)

    print('\n== POST SEM AS SEÇÕES NOVAS (tela antiga / script) ==')
    antigo = {k: v for k, v in form_da_tela(c.get(url).content.decode()).items()
              if k not in NOVOS and not k.startswith(('emergency_', 'dependent_'))
              and not k.endswith('__na_tela')}
    r = c.post(url, antigo)
    alvo.refresh_from_db()
    t('redireciona', r.status_code == 302, r.status_code)
    t('campos novos não foram apagados',
      all(getattr(alvo, k) == v for k, v in esperado.items()),
      {k: getattr(alvo, k) for k, v in esperado.items() if getattr(alvo, k) != v})
    t('listas intactas', alvo.emergency_contacts.count() == 2 and alvo.dependents.count() == 2)

    print('\n== ERROS VOLTAM SEM GRAVAR ==')
    base = form_da_tela(c.get(url).content.decode())
    r = c.post(url, trocar(base, tangerino_employee_id='555', rg='NAO-GRAVA'))
    alvo.refresh_from_db()
    t('ID do Tangerino de outra pessoa é recusado', r.status_code == 302 and r.url == url
      and alvo.tangerino_employee_id == 777 and alvo.rg == 'MG-12.345', (r.status_code, getattr(r, 'url', '')))
    r = c.post(url, trocar(base, tangerino_employee_id='abc', rg='NAO-GRAVA'))
    alvo.refresh_from_db()
    t('ID não numérico é recusado', alvo.rg == 'MG-12.345' and alvo.tangerino_employee_id == 777)
    r = c.post(url, trocar(base, emergency_name_3='Só nome', rg='NAO-GRAVA'))
    alvo.refresh_from_db()
    t('contato pela metade é recusado', alvo.rg == 'MG-12.345' and alvo.emergency_contacts.count() == 2)
    r = c.post(url, trocar(base, dependent_name_3='Sem doc', rg='NAO-GRAVA'))
    alvo.refresh_from_db()
    t('dependente pela metade é recusado', alvo.rg == 'MG-12.345' and alvo.dependents.count() == 2)
    r = c.post(url, trocar(base, rg_issue_date='31/02/2010', rg='NAO-GRAVA'))
    alvo.refresh_from_db()
    t('data de emissão inválida é recusada', alvo.rg == 'MG-12.345' and alvo.rg_issue_date == date(2010, 3, 4))
    r = c.post(url, trocar(base, sex='XYZ', skin_color='ROXA'))
    alvo.refresh_from_db()
    t('escolha inexistente vira vazio', alvo.sex == '' and alvo.skin_color == '')

    print('\n== "NÃO POSSUI DEPENDENTES" E REMOVER CONTATO ==')
    base = form_da_tela(c.get(url).content.decode())
    r = c.post(url, trocar(base, has_dependents='nao', emergency_name_2='', emergency_phone_2='',
                           emergency_relationship_2='', tangerino_employee_id=''))
    alvo.refresh_from_db()
    t('dependentes apagados com "Não"', alvo.has_dependents is False and alvo.dependents.count() == 0)
    t('contato removido', list(alvo.emergency_contacts.values_list('name', flat=True)) == ['Mãe'])
    t('ID do Tangerino em branco vira vazio', alvo.tangerino_employee_id is None)

    print('\n== QUEM NÃO PODE CONTINUA SEM ACESSO ==')
    comum = User.objects.create_user(username='zz.edcomum', email='zz.edcomum@exemplo-teste.local',
                                     password='x', hierarchy='PADRAO', sector=loja)
    c2 = Client()
    c2.force_login(comum)
    r = c2.post(url, trocar(base, rg='INVASOR'))
    alvo.refresh_from_db()
    t('PADRAO é barrado e nada muda', r.status_code == 302 and alvo.rg != 'INVASOR')

    t('nenhum e-mail saiu', len(mail.outbox) == 0, len(mail.outbox))
finally:
    pilha.close()
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
