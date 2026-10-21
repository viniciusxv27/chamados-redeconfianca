"""Perfil e grupos (21/10/2026).

Pedido 1: em /users/manage/users/{id}/profile/ separar os documentos enviados no
pré-cadastro dos adicionados depois.
Pedido 2: em /users/manage/groups/{id}/edit/ escolher cargos (os do "Cargo" da
edição do usuário) que põem a pessoa no grupo sozinha; mudou o cargo, sai;
vários cargos no mesmo grupo.

Transação desfeita no fim; o arquivo vai para um storage em memória (nada no MinIO).
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-gc'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-gc2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext

from communications.cargos import normalizar
from communications.models import CargoDoGrupo, CommunicationGroup, MembroPeloCargo
from users import views as uv
from users.models import Sector, User, UserDocument

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


def membros(g):
    return set(g.members.values_list('id', flat=True))


def auto(g):
    return set(MembroPeloCargo.objects.filter(grupo=g).values_list('user_id', flat=True))


campo_arquivo = UserDocument._meta.get_field('file')
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(campo_arquivo, 'storage', InMemoryStorage()):
        loja = Sector.objects.create(name='Loja ZZ Grupos')
        adm = User.objects.create_user(username='zz.gc.adm', email='zz.gc.adm@exemplo-teste.local', password='S3nha!teste',
                                       first_name='ZZ', last_name='Gestora', hierarchy='SUPERADMIN', sector=loja)
        ca = Client()
        ca.force_login(adm)

        print('— Documentos: pré-cadastro × depois')
        novo = User.objects.create_user(username='zz.gc.novo', email='zz.gc.novo@exemplo-teste.local', password='x',
                                        first_name='ZZ', last_name='Novata', sector=loja,
                                        pre_registration_status=User.PRE_REG_APPROVED)
        d_pre = uv._store_user_document(novo, SimpleUploadedFile('rg.pdf', b'%PDF-1.4 zz', 'application/pdf'),
                                        document_name='RG ZZ', uploaded_by=novo, origem=UserDocument.ORIGEM_PRE_CADASTRO)
        d_antigo = UserDocument.objects.create(user=novo, document_name='CPF antigo ZZ', file='zz/cpf.pdf',
                                               original_filename='cpf.pdf', uploaded_by=novo)
        r = ca.post(f'/users/manage/users/{novo.pk}/documents/upload/',
                    {'document_name': 'Contrato ZZ', 'file': SimpleUploadedFile('contrato.pdf', b'%PDF-1.4 zz', 'application/pdf')})
        d_gestor = UserDocument.objects.get(user=novo, document_name='Contrato ZZ')
        t('upload do gestor grava "adicionado depois"', d_gestor.origem == UserDocument.ORIGEM_POSTERIOR
          and d_gestor.uploaded_by == adm)
        t('documento do pré-cadastro grava a origem', d_pre.origem == UserDocument.ORIGEM_PRE_CADASTRO and d_pre.do_pre_cadastro)
        t('documento antigo (sem origem) enviado pela própria pessoa conta como pré-cadastro',
          d_antigo.origem is None and d_antigo.do_pre_cadastro)
        fonte = open('users/views.py').read()
        t('os dois caminhos do pré-cadastro passam a origem',
          fonte.count('origem=UserDocument.ORIGEM_PRE_CADASTRO') == 2)
        r = ca.get(f'/users/manage/users/{novo.pk}/profile/')
        h = html(r)
        i_pre, i_dep = h.find('Enviados no pré-cadastro'), h.find('Adicionados depois')
        t('perfil abre com as duas seções', r.status_code == 200 and 0 < i_pre < i_dep, r.status_code)
        t('RG e CPF na seção do pré-cadastro', i_pre < h.find('RG ZZ') < i_dep and i_pre < h.find('CPF antigo ZZ') < i_dep)
        t('contrato na seção de depois, com quem enviou', h.find('Contrato ZZ') > i_dep and 'por ZZ Gestora' in h[i_dep:])
        t('contadores por seção', '(2)' in h[i_pre:i_dep] and '(1)' in h[i_dep:i_dep + 400])
        normal = User.objects.create_user(username='zz.gc.normal', email='zz.gc.normal@exemplo-teste.local', password='x',
                                          first_name='ZZ', last_name='Normal', sector=loja)
        UserDocument.objects.create(user=normal, document_name='Doc ZZ', file='zz/d.pdf', original_filename='d.pdf',
                                    uploaded_by=adm, origem=UserDocument.ORIGEM_POSTERIOR)
        h = html(ca.get(f'/users/manage/users/{normal.pk}/profile/'))
        t('sem pré-cadastro: sem a seção vazia do pré-cadastro', 'Enviados no pré-cadastro' not in h
          and 'Adicionados pela gestão' in h)

        print('— Grupo por cargo')
        g = CommunicationGroup.objects.create(name='ZZ Gerentes', created_by=adm)
        a = User.objects.create_user(username='zz.gc.a', email='zz.gc.a@exemplo-teste.local', password='x',
                                     first_name='ZZ', last_name='Ana', job_title='GERENTE DE LOJA ZZ', sector=loja)
        b = User.objects.create_user(username='zz.gc.b', email='zz.gc.b@exemplo-teste.local', password='x',
                                     first_name='ZZ', last_name='Bia', job_title='Consultor ZZ', sector=loja)
        c = User.objects.create_user(username='zz.gc.c', email='zz.gc.c@exemplo-teste.local', password='x',
                                     first_name='ZZ', last_name='Caio', job_title='Gerente de loja ZZ', sector=loja)
        d = User.objects.create_user(username='zz.gc.d', email='zz.gc.d@exemplo-teste.local', password='x',
                                     first_name='ZZ', last_name='Duda', job_title='Coordenador ZZ', sector=loja)
        g.members.add(c)  # posto à mão antes da regra existir
        h = html(ca.get(f'/users/manage/groups/{g.pk}/edit/'))
        t('tela lista os cargos do cadastro com a contagem', 'value="GERENTE DE LOJA ZZ"' in h and 'Coordenador ZZ' in h
          and 'Entrar pelo cargo' in h)
        t('variações do mesmo cargo viram uma opção só', h.count('value="GERENTE DE LOJA ZZ"') + h.count('value="Gerente de loja ZZ"') == 1)
        r = ca.post(f'/users/manage/groups/{g.pk}/edit/', {'name': g.name, 'description': '', 'users': [c.pk],
                                                          'cargos': ['GERENTE DE LOJA ZZ']})
        t('salvar volta para a lista', r.status_code == 302)
        t('quem tem o cargo entra sozinho (sem acento/caixa/espaço)', a.pk in membros(g) and a.pk in auto(g))
        t('quem estava à mão continua à mão', c.pk in membros(g) and c.pk not in auto(g))
        t('outro cargo não entra', b.pk not in membros(g))
        a.job_title = 'Consultor ZZ'
        a.save()
        t('mudou o cargo: sai do grupo', a.pk not in membros(g) and a.pk not in auto(g))
        b.job_title = '  gerente  de loja zz '
        b.save()
        t('ganhou o cargo: entra', b.pk in membros(g) and b.pk in auto(g))
        c.job_title = 'Consultor ZZ'
        c.save()
        t('quem foi posto à mão não sai ao mudar de cargo', c.pk in membros(g))

        print('— Mais de um cargo no grupo')
        h = html(ca.get(f'/users/manage/groups/{g.pk}/edit/'))
        t('membro pelo cargo aparece travado', f'id="user_{b.pk}" data-pelo-cargo' in h and 'pelo cargo' in h)
        r = ca.post(f'/users/manage/groups/{g.pk}/edit/', {'name': g.name, 'description': '', 'users': [c.pk],
                                                          'cargos': ['GERENTE DE LOJA ZZ', 'Coordenador ZZ']})
        t('dois cargos gravados', set(CargoDoGrupo.objects.filter(grupo=g).values_list('cargo', flat=True))
          == {'GERENTE DE LOJA ZZ', 'Coordenador ZZ'})
        t('quem tem o segundo cargo entra', d.pk in membros(g) and d.pk in auto(g))
        t('quem entrou pelo cargo continua (caixa travada não vai no POST)', b.pk in membros(g))
        r = ca.post(f'/users/manage/groups/{g.pk}/edit/', {'name': g.name, 'description': '', 'users': [c.pk],
                                                          'cargos': ['GERENTE DE LOJA ZZ']})
        t('tirar o cargo do grupo tira quem entrou por ele', d.pk not in membros(g) and b.pk in membros(g))
        t('mensagem conta quem saiu', 'saiu' in ' '.join(m.message for m in r.wsgi_request._messages))

        print('— Outros caminhos')
        e = User.objects.create_user(username='zz.gc.e', email='zz.gc.e@exemplo-teste.local', password='x',
                                     first_name='ZZ', last_name='Edu', job_title='Gerente de Loja ZZ', sector=loja)
        t('usuário novo com o cargo já nasce no grupo', e.pk in membros(g) and e.pk in auto(g))
        b.is_active = False
        b.save()
        t('desligado sai do grupo', b.pk not in membros(g))
        with CaptureQueriesContext(connection) as q:
            e.save(update_fields=['last_login'])
        t('save que não mexe em cargo não consulta os grupos',
          not any('communications_' in x['sql'] for x in q.captured_queries), len(q.captured_queries))
        parcial = User.objects.only('id').get(pk=e.pk)
        with CaptureQueriesContext(connection) as q:
            list(User.objects.only('id', 'first_name').filter(pk__in=[a.pk, e.pk]))
        t('carregar usuário pela metade não faz consulta extra', len(q.captured_queries) == 1, len(q.captured_queries))
        parcial.first_name = 'ZZ2'
        parcial.save(update_fields=['first_name'])
        t('salvar instância parcial não quebra nem tira do grupo', e.pk in membros(g))
        t('normalizar ignora acento, caixa e espaço', normalizar(' Gerênte  de LOJA ') == 'GERENTE DE LOJA')
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
