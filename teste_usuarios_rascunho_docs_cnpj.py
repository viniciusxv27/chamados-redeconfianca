"""Cadastro de usuários (26/11/2026).

Pedidos: em /users/manage/users/{id}/edit/ guardar no navegador o que foi digitado (por pessoa editada)
e mandar vários documentos de uma vez ("Adicionar mais"); em /users/manage/users/pre-register/ o campo
"CNPJ Filial" que já existe no cadastro.

Transação desfeita no fim; os arquivos vão para um storage em memória (nada no MinIO); requests bloqueado.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-urd'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-urd2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client

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


def pdf(nome):
    return SimpleUploadedFile(nome, b'%PDF-1.4 zz', 'application/pdf')


campo_arquivo = UserDocument._meta.get_field('file')
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(campo_arquivo, 'storage', InMemoryStorage()):
        loja = Sector.objects.create(name='Loja ZZ Cadastro')
        adm = User.objects.create_user(username='zz.urd.adm', email='zz.urd.adm@exemplo-teste.local', password='S3nha!teste',
                                       first_name='ZZ', last_name='Gestora', hierarchy='SUPERADMIN', sector=loja)
        alvo = User.objects.create_user(username='zz.urd.alvo', email='zz.urd.alvo@exemplo-teste.local', password='x',
                                        first_name='ZZ', last_name='Colaboradora', sector=loja)
        ca = Client()
        ca.force_login(adm)

        print('— Edição: rascunho no navegador')
        r = ca.get(f'/users/manage/users/{alvo.pk}/edit/')
        h = html(r)
        t('edição abre', r.status_code == 200)
        t('formulário identificado para o rascunho', 'id="formEditarUsuario"' in h and 'data-erro-ao-salvar=""' in h)
        t('chave do rascunho é por pessoa editada', f"rc:rascunho:edicao\\u002Dusuario:{alvo.pk}'" in h)
        t('rascunho guarda só o que mudou e ignora arquivo/senha/oculto', "file: 1, password: 1, hidden: 1" in h)
        r = ca.post(f'/users/manage/users/{alvo.pk}/edit/', {'email': adm.email, 'username': alvo.username,
                                                            'full_name': 'ZZ Colaboradora', 'hierarchy': 'PADRAO'})
        if r.status_code == 302:
            r = ca.get(r['Location'])
        t('salvar com erro volta marcado (o rascunho é reaplicado)', 'data-erro-ao-salvar="1"' in html(r), r.status_code)
        r = ca.get(f'/users/manage/users/{alvo.pk}/edit/')
        t('sem erro na tela, o rascunho enviado é descartado', 'data-erro-ao-salvar=""' in html(r))

        print('— Vários documentos de uma vez')
        h = html(ca.get(f'/users/manage/users/{alvo.pk}/edit/'))
        t('formulário de documentos com "Adicionar mais"', 'data-doc-mais' in h and 'Adicionar mais' in h and 'multiple' in h)
        antes = UserDocument.objects.filter(user=alvo).count()
        r = ca.post(f'/users/manage/users/{alvo.pk}/documents/upload/', {
            'voltar': f'/users/manage/users/{alvo.pk}/edit/',
            'categoria_0': 'PESSOAL', 'document_type_0': '', 'document_name_0': 'RG ZZ', 'file_0': [pdf('rg-frente.pdf'), pdf('rg-verso.pdf')],
            'categoria_3': '', 'document_type_3': '', 'document_name_3': 'Contrato ZZ', 'file_3': pdf('contrato.pdf'),
            'categoria_5': '', 'document_type_5': '', 'document_name_5': 'Linha sem arquivo',
        })
        docs = list(UserDocument.objects.filter(user=alvo).order_by('id'))
        t('três arquivos em duas linhas viram três documentos', len(docs) == antes + 3, len(docs))
        t('categoria e nome de cada linha', [(d.document_name, d.categoria) for d in docs[-3:]]
          == [('RG ZZ', 'PESSOAL'), ('RG ZZ', 'PESSOAL'), ('Contrato ZZ', '')], [(d.document_name, d.categoria) for d in docs[-3:]])
        t('quem enviou e origem "depois"', all(d.uploaded_by == adm and d.origem == UserDocument.ORIGEM_POSTERIOR for d in docs[-3:]))
        t('volta para a edição, na parte dos documentos', r.status_code == 302 and r['Location'].endswith(f'/users/manage/users/{alvo.pk}/edit/#documentos'))
        mensagens = [str(m) for m in r.wsgi_request._messages]
        t('aviso com o total', '3 documentos adicionados.' in mensagens, mensagens)

        antes = UserDocument.objects.filter(user=alvo).count()
        r = ca.post(f'/users/manage/users/{alvo.pk}/documents/upload/', {
            'document_name_0': 'Bom ZZ', 'file_0': pdf('bom.pdf'),
            'document_name_1': 'Ruim ZZ', 'file_1': SimpleUploadedFile('virus.exe', b'MZ', 'application/octet-stream'),
        })
        mensagens = [str(m) for m in r.wsgi_request._messages]
        t('arquivo recusado não impede os outros', UserDocument.objects.filter(user=alvo).count() == antes + 1
          and UserDocument.objects.filter(user=alvo, document_name='Bom ZZ').exists())
        t('recusa diz a linha e o motivo', any(m.startswith('Não enviado — Linha 2: O arquivo "virus.exe"') for m in mensagens), mensagens)

        antes = UserDocument.objects.filter(user=alvo).count()
        ca.post(f'/users/manage/users/{alvo.pk}/documents/upload/', {'document_name': 'Antigo ZZ', 'file': pdf('a.pdf')})
        t('formulário antigo (um documento, sem sufixo) continua valendo',
          UserDocument.objects.filter(user=alvo).count() == antes + 1 and UserDocument.objects.filter(document_name='Antigo ZZ').exists())
        r = ca.post(f'/users/manage/users/{alvo.pk}/documents/upload/', {'document_name_0': 'Nada'})
        t('sem arquivo nenhum: pede o arquivo', 'Selecione um arquivo para enviar.' in [str(m) for m in r.wsgi_request._messages])
        r = ca.post(f'/users/manage/users/{alvo.pk}/documents/upload/', {'categoria_0': 'NAO_EXISTE', 'file_0': pdf('x.pdf')})
        t('categoria inválida recusada', any('categoria' in str(m) for m in r.wsgi_request._messages)
          and not UserDocument.objects.filter(original_filename='x.pdf', user=alvo).exists())
        comum = User.objects.create_user(username='zz.urd.comum', email='zz.urd.comum@exemplo-teste.local', password='x',
                                         first_name='ZZ', hierarchy='PADRAO', sector=loja)
        cc = Client()
        cc.force_login(comum)
        antes = UserDocument.objects.filter(user=alvo).count()
        cc.post(f'/users/manage/users/{alvo.pk}/documents/upload/', {'file_0': pdf('y.pdf')})
        t('sem permissão não sobe documento', UserDocument.objects.filter(user=alvo).count() == antes)
        h = html(ca.get(f'/users/manage/users/{alvo.pk}/profile/'))
        t('perfil usa o mesmo formulário com "Adicionar mais"', 'data-doc-mais' in h)

        print('— Categoria nova pela tela de documentos')
        from users.models import CategoriaDocumento, PermissaoCategoriaDocumento, categorias_documento
        h = html(ca.get(f'/users/manage/users/{alvo.pk}/edit/'))
        t('opção "+ Nova categoria…" e botão "Nova categoria"', '<option value="__nova__">+ Nova categoria…</option>' in h
          and 'data-cat-abrir' in h and '/users/manage/documentos/categorias/nova/' in h)
        t('SUPERADMIN escolhe quem vê (3 opções)', h.count('<option value="SUPERADMIN">Só o SUPERADMIN</option>') == 1
          and '<option value="RH_DP">' in h)
        URL_NOVA = '/users/manage/documentos/categorias/nova/'
        r = ca.post(URL_NOVA, {'nome': '  Treinamentos   ZZ ', 'acesso': 'GESTORES'})
        j = r.json()
        cat = CategoriaDocumento.objects.filter(nome='Treinamentos ZZ').first()
        t('cria a categoria (nome limpo, chave CAT…)', r.status_code == 200 and j['ok'] and cat is not None
          and j['chave'] == cat.chave and cat.chave.startswith('CAT') and len(cat.chave) <= 14 and cat.criada_por == adm, j)
        t('entra na lista de categorias antes de "Outros"', [c for c, _ in categorias_documento()][-2:] == [cat.chave, 'OUTROS'])
        j = ca.post(URL_NOVA, {'nome': 'treinamentos zz'}).json()
        t('mesmo nome (sem maiúscula) devolve a que existe', j['ok'] and j['ja_existia'] and j['chave'] == cat.chave
          and CategoriaDocumento.objects.filter(nome__iexact='treinamentos zz').count() == 1)
        j = ca.post(URL_NOVA, {'nome': 'Declarações'}).json()
        t('nome de categoria fixa devolve a fixa', j['ja_existia'] and j['chave'] == 'DECLARACAO')
        t('nome curto recusado', ca.post(URL_NOVA, {'nome': 'ab'}).status_code == 400)
        t('quem não gerencia cadastros não cria', cc.post(URL_NOVA, {'nome': 'ZZ Proibida'}).status_code == 403
          and not CategoriaDocumento.objects.filter(nome='ZZ Proibida').exists())
        antes = UserDocument.objects.filter(user=alvo).count()
        ca.post(f'/users/manage/users/{alvo.pk}/documents/upload/', {'categoria_0': cat.chave, 'document_name_0': 'NR-10 ZZ',
                                                                     'file_0': pdf('nr10.pdf')})
        d = UserDocument.objects.filter(user=alvo, document_name='NR-10 ZZ').first()
        t('documento sobe na categoria nova', d is not None and d.categoria == cat.chave and d.categoria_efetiva == cat.chave)
        h = html(ca.get(f'/users/manage/users/{alvo.pk}/edit/'))
        i_cat = h.find(f'id="doc-{cat.chave.lower()}"')
        t('a tela ganha a seção da categoria nova, com o documento', i_cat > 0 and h.find('NR-10 ZZ', i_cat) > 0
          and 'Treinamentos ZZ' in h)
        outro = UserDocument.objects.filter(user=alvo, document_name='Bom ZZ').first()
        r = ca.post(f'/users/manage/user-documents/{outro.pk}/categoria/', {'categoria': cat.chave})
        outro.refresh_from_db()
        t('reclassificar para a categoria nova', r.json()['ok'] and outro.categoria == cat.chave)
        t('a categoria nova aparece no acesso por categoria', 'Treinamentos ZZ' in html(ca.get('/users/manage/documentos/permissoes/')))

        gestor = User.objects.create_user(username='zz.urd.gestor', email='zz.urd.gestor@exemplo-teste.local', password='x',
                                          first_name='ZZ', last_name='Supervisor', hierarchy='SUPERVISOR', sector=loja)
        cg = Client()
        cg.force_login(gestor)
        h = html(cg.get(f'/users/manage/users/{alvo.pk}/profile/'))
        t('quem não é do RH só cria categoria que ele mesmo vê', 'data-cat-abrir' in h and '<option value="SUPERADMIN">Só o SUPERADMIN' not in h
          and '<option value="RH_DP">' not in h)
        r = cg.post(URL_NOVA, {'nome': 'ZZ Escondida', 'acesso': 'SUPERADMIN'})
        t('…e não cria categoria escondida dele', r.status_code == 403 and not CategoriaDocumento.objects.filter(nome='ZZ Escondida').exists())
        j = cg.post(URL_NOVA, {'nome': 'ZZ Uniformes'}).json()
        t('supervisor cria categoria aberta a quem gerencia cadastros', j['ok'] and not j['ja_existia'])
        j = ca.post(URL_NOVA, {'nome': 'ZZ Saúde ocupacional', 'acesso': 'RH_DP'}).json()
        restrita = CategoriaDocumento.objects.get(chave=j['chave'])
        t('categoria restrita grava a regra de acesso', PermissaoCategoriaDocumento.objects.filter(
            categoria=restrita.chave, acesso='RH_DP').exists())
        j = cg.post(URL_NOVA, {'nome': 'zz saude ocupacional'}).json()
        t('nome de categoria restrita não vaza para quem não vê', not j['ok'] and 'acesso restrito' in j['erro'])
        antes = UserDocument.objects.filter(user=alvo).count()
        cg.post(f'/users/manage/users/{alvo.pk}/documents/upload/', {'categoria_0': restrita.chave, 'file_0': pdf('z.pdf')})
        t('supervisor não sobe documento na categoria restrita', UserDocument.objects.filter(user=alvo).count() == antes)
        h = html(cg.get(f'/users/manage/users/{alvo.pk}/profile/'))
        t('…nem a vê na lista de categorias', 'Saúde ocupacional' not in h and 'ZZ Uniformes' in h)

        print('— Pré-cadastro: CNPJ Filial')
        User.objects.filter(pk=alvo.pk).update(branch_cnpj='39410478/000125')
        r = ca.get('/users/manage/users/pre-register/')
        h = html(r)
        t('campo CNPJ Filial no pré-cadastro', r.status_code == 200 and 'name="branch_cnpj"' in h and 'CNPJ Filial' in h)
        t('sugere os CNPJs já usados (formatados, sem repetir)', '<option value="39.410.478/0001-25">' in h
          and '39410478/000125' not in h)
        t('formato do CNPJ', uv._cnpj_filial(' 39410478000125 ') == ('39.410.478/0001-25', True)
          and uv._cnpj_filial('') == ('', True) and uv._cnpj_filial('123')[1] is False)
        dados = {'full_name': 'ZZ Nova Pessoa', 'email': 'zz.urd.nova@exemplo-teste.local', 'sector': loja.pk,
                 'hierarchy': 'PADRAO', 'admission_date': '2026-12-01', 'branch_cnpj': '39410478000125'}
        r = ca.post('/users/manage/users/pre-register/', dados)
        nova = User.objects.filter(email='zz.urd.nova@exemplo-teste.local').first()
        t('pré-cadastro grava o CNPJ Filial formatado', nova is not None and nova.branch_cnpj == '39.410.478/0001-25',
          nova and nova.branch_cnpj)
        r = ca.post('/users/manage/users/pre-register/', {**dados, 'email': 'zz.urd.outra@exemplo-teste.local', 'branch_cnpj': '3941047'})
        t('CNPJ incompleto: recusa e mantém o que foi digitado', not User.objects.filter(email='zz.urd.outra@exemplo-teste.local').exists()
          and 'precisa ter 14 números' in html(r) and 'value="3941047"' in html(r))
        r = ca.post('/users/manage/users/pre-register/', {**dados, 'email': 'zz.urd.sem@exemplo-teste.local', 'branch_cnpj': ''})
        sem = User.objects.filter(email='zz.urd.sem@exemplo-teste.local').first()
        t('CNPJ Filial é opcional', sem is not None and sem.branch_cnpj == '')
        h = html(ca.get(f'/users/manage/users/{nova.pk}/edit/'))
        t('a edição mostra o CNPJ que veio do pré-cadastro', 'value="39.410.478/0001-25"' in h)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco; nada no MinIO.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
