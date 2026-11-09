"""Pré-cadastro: erro de rede ou inesperado não pode perder os dados (09/11/2026).

Pedido: "Ao pessoa concluir o pré cadastro, se der erro de rede ou algum erro
inesperado, trate para não perder os dados e conseguir corrigir para enviar novamente".

- O formulário envia por static/js/envio-seguro.js (cabeçalho X-Envio-Seguro): a view
  responde JSON e a página não é trocada — validação vira lista de erros, falha ao
  salvar vira 500 em JSON, e nada fica pela metade (a transação desfaz tudo).
- Sem o script (envio normal), a falha ao salvar volta para a tela com o que foi digitado.
- O MinIO não é tocado: _store_user_document é trocado por um dublê.

Transação desfeita no fim: nada é gravado, nada sai.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-envio'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-envio-local'},
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

from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile

from users import views as uv
from users.models import RequiredDocument, Sector

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
    'full_name': 'ZZEnvio Colaborador Novo', 'cpf': '390.533.447-05',
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
    'emergency_name_1': 'Contato Teste', 'emergency_phone_1': '(27) 98888-0000',
    'emergency_relationship_1': 'Irmão', 'has_dependents': 'nao',
    'password': 'S3nha!teste', 'password_confirm': 'S3nha!teste',
}
SEGURO = {'HTTP_X_ENVIO_SEGURO': '1', 'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not User.objects.filter(username__startswith='zzenvio.').exists(), 'usuários do teste já existem'
    area = Sector.objects.create(name='ZZ Area Envio Seguro')
    RequiredDocument.objects.update(is_active=False)          # desfeito no rollback
    doc = RequiredDocument.objects.create(name='ZZ Documento de identidade', is_required=True, is_active=True)

    def novo(apelido, token):
        u = User.objects.create_user(username=f'zzenvio.{apelido}', email=f'zzenvio.{apelido}@exemplo-teste.local',
                                     sector=area, first_name='ZZEnvio', last_name=apelido.title(), is_active=False)
        u.set_unusable_password()
        u.pre_registration_status = User.PRE_REG_PENDING
        u.pre_registration_token = token
        u.pre_registration_created_at = timezone.now()
        u.save()
        return u

    def ficha(com_doc=True, **extra):
        dados = dict(FICHA)
        dados.update(extra)
        if com_doc:
            dados[f'document_{doc.id}'] = SimpleUploadedFile('rg.pdf', b'%PDF-1.4 zz', content_type='application/pdf')
        return dados

    pessoa = novo('pessoa', 'zz-token-envio-seguro-1')
    url = f'/users/pre-cadastro/{pessoa.pre_registration_token}/'
    c = Client()

    print('== A TELA ==')
    html = c.get(url).content.decode()
    t('o formulário usa o envio seguro com rascunho por pessoa',
      'data-envio-seguro' in html and f'data-rascunho="precadastro-{pessoa.pk}"' in html and 'js/envio-seguro.js' in html)
    t('o script das fotos vem antes (ele encolhe e reenvia)', html.index('upload-imagem.js') < html.index('envio-seguro.js'))

    print('\n== ERRO DE VALIDAÇÃO ==')
    r = c.post(url, ficha(com_doc=False, password_confirm='outra'), **SEGURO)
    j = r.json()
    t('volta 400 em JSON com a lista de erros (a página não troca)', r.status_code == 400 and not j['ok']
      and any('obrigatório' in e for e in j['erros']) and any('senhas' in e.lower() for e in j['erros']), j)
    pessoa.refresh_from_db()
    t('nada foi gravado e o link continua valendo', pessoa.pre_registration_status == User.PRE_REG_PENDING
      and pessoa.pre_registration_token == 'zz-token-envio-seguro-1' and not pessoa.is_active)

    print('\n== ERRO INESPERADO AO SALVAR (MinIO/banco fora) ==')
    with mock.patch.object(uv, '_store_user_document', side_effect=OSError('MinIO fora do ar')):
        r = c.post(url, ficha(), **SEGURO)
    j = r.json()
    t('volta 500 em JSON com a mensagem para reenviar', r.status_code == 500 and j.get('erro_inesperado')
      and 'continuam na tela' in j['erros'][0], (r.status_code, j))
    pessoa.refresh_from_db()
    t('a transação desfez tudo: ainda pendente, sem senha e com o link valendo',
      pessoa.pre_registration_status == User.PRE_REG_PENDING and not pessoa.is_active
      and pessoa.pre_registration_token == 'zz-token-envio-seguro-1' and not pessoa.has_usable_password()
      and pessoa.cpf != '39053344705' and not pessoa.emergency_contacts.exists() if hasattr(pessoa, 'emergency_contacts')
      else pessoa.pre_registration_status == User.PRE_REG_PENDING)

    print('\n== REENVIO DEPOIS DA FALHA ==')
    with mock.patch.object(uv, '_store_user_document') as guardar:
        r = c.post(url, ficha(), **SEGURO)
    j = r.json()
    t('o mesmo envio de novo funciona e manda para a tela de concluído', r.status_code == 200 and j['ok']
      and j['redirect'] == '/users/pre-cadastro/concluido/', j)
    pessoa.refresh_from_db()
    t('cadastro concluído, conta ativa e documento guardado', pessoa.pre_registration_status == User.PRE_REG_COMPLETED
      and pessoa.is_active and pessoa.check_password('S3nha!teste') and guardar.call_count == 1)
    r = c.post(url, ficha(), **SEGURO)
    t('reenviar depois de recebido (resposta perdida na rede) explica em vez de "link inválido" seco',
      r.status_code == 410 and r.json().get('link_invalido') and 'pode ter sido' in r.json()['erros'][0])

    print('\n== SEM O SCRIPT (ENVIO NORMAL) ==')
    outra = novo('outra', 'zz-token-envio-seguro-2')
    url2 = f'/users/pre-cadastro/{outra.pre_registration_token}/'
    with mock.patch.object(uv, '_store_user_document', side_effect=OSError('MinIO fora do ar')):
        r = c.post(url2, ficha())
    html = r.content.decode()
    t('falha ao salvar volta para o formulário com o que foi digitado (não dá 500)', r.status_code == 200
      and 'continuam na tela' in html and 'value="ZZEnvio Colaborador Novo"' in html, r.status_code)
    with mock.patch.object(uv, '_store_user_document'):
        r = c.post(url2, ficha())
    t('e o envio normal continua redirecionando no sucesso', r.status_code == 302 and r['Location'] == '/users/pre-cadastro/concluido/')

    print('\n== AJUSTE APÓS REPROVAÇÃO ==')
    reprovada = User.objects.create_user(username='zzenvio.reprovada', email='zzenvio.reprovada@exemplo-teste.local',
                                         sector=area, first_name='ZZEnvio', last_name='Reprovada', password='S3nha!teste')
    reprovada.pre_registration_status = User.PRE_REG_REJECTED
    reprovada.save()
    reprovada.pre_registration_extra_documents.add(doc)
    ca = Client(); ca.force_login(reprovada)
    html = ca.get('/users/pre-cadastro/ajustar/').content.decode()
    t('a tela de ajuste também usa o envio seguro', 'data-envio-seguro' in html and 'js/envio-seguro.js' in html)
    r = ca.post('/users/pre-cadastro/ajustar/', ficha(com_doc=False), **SEGURO)
    t('ajuste sem o documento pedido: 400 com o erro', r.status_code == 400 and 'precisa ser anexado' in r.json()['erros'][0])
    with mock.patch.object(uv, '_store_user_document', side_effect=OSError('MinIO fora do ar')):
        r = ca.post('/users/pre-cadastro/ajustar/', ficha(), **SEGURO)
    reprovada.refresh_from_db()
    t('ajuste com falha ao salvar: 500 em JSON e continua reprovado (pode reenviar)',
      r.status_code == 500 and reprovada.pre_registration_status == User.PRE_REG_REJECTED)
    with mock.patch.object(uv, '_store_user_document'):
        r = ca.post('/users/pre-cadastro/ajustar/', ficha(), **SEGURO)
    reprovada.refresh_from_db()
    t('ajuste reenviado: JSON ok e volta para a análise', r.status_code == 200 and r.json()['redirect'] == '/'
      and reprovada.pre_registration_status == User.PRE_REG_COMPLETED, r.content[:200])
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
