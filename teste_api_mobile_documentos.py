"""App mobile — módulo Documentos (/api/mobile/v1/m/documentos/).

Roda dentro de uma transação desfeita no fim. O PDF original e o PDF assinado vão
para um storage em memória (o campo é trocado só durante o teste): nada sobe
para o MinIO.
"""
import base64
import io
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Cache em memória: o `default` é o Redis remoto do dev e limpar ele seria um FLUSHDB do ambiente todo.
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings
from django.core.cache import caches

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import pypdfium2 as pdfium
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.db import transaction
from django.test import Client

from documentos.models import Document, DocumentCategory, DocumentSignature
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/documentos'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def pdf_de_teste():
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


PNG = 'data:image/png;base64,' + base64.b64encode(bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082')).decode()


def forjar(user, tipo, obj_id):
    return f'{BASE}/arquivo/' + signing.dumps({'u': user.pk, 't': tipo, 'i': str(obj_id)},
                                               salt='api_mobile.arquivo', compress=True) + '/'


for nome in settings.CACHES:
    caches[nome].clear()

memoria = InMemoryStorage()
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(Document._meta.get_field('pdf_file'), 'storage', memoria), \
            mock.patch.object(DocumentSignature._meta.get_field('signed_pdf'), 'storage', memoria):
        setor = Sector.objects.create(name='ZZ Setor Documentos App')

        def novo(apelido, hierarquia='PADRAO', **kw):
            return User.objects.create_user(
                username=f'zzdoc.{apelido}', email=f'zzdoc.{apelido}@exemplo-teste.local', password='S3nha!teste',
                first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia, **kw)

        dono = novo('dono')
        outro = novo('outro')
        admin = novo('admin', 'ADMIN')            # ADMINISTRAÇÃO: can_manage_rh

        cat_a = DocumentCategory.objects.create(name='ZZ Contratos App', color='blue')
        cat_b = DocumentCategory.objects.create(name='ZZ Termos App', color='green')

        def documento(titulo, cat, visivel=True):
            d = Document(title=titulo, category=cat, description='Leia com atenção.', is_visible=visivel)
            d.pdf_file.save('zz-doc.pdf', ContentFile(pdf_de_teste()), save=False)
            d.save()
            return d

        d1 = documento('ZZ Contrato', cat_a)
        d2 = documento('ZZ Termo de uso', cat_b)
        d_oculto = documento('ZZ Ainda não liberado', cat_a, visivel=False)
        s1 = DocumentSignature.objects.create(document=d1, user=dono)
        s2 = DocumentSignature.objects.create(document=d2, user=dono)
        s_oculto = DocumentSignature.objects.create(document=d_oculto, user=dono)
        s_outro = DocumentSignature.objects.create(document=d1, user=outro)

        def entrar(user):
            r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste',
                                                      'plataforma': 'android', 'dispositivo': 'ZZ'},
                              content_type='application/json')
            return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

        cd, co, ca = entrar(dono), entrar(outro), entrar(admin)
        anon = Client()

        print('== ENTRADA ==')
        t('aparece em /modulos/ para todo logado (como o menu da web)',
          'documentos' in [m['chave'] for m in cd.get(f'{BASE}/modulos/').json()])
        t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))
        from api_mobile.modulos import documentos as mod
        with mock.patch.dict(mod.MODULO, {'pode_entrar': lambda u: False}):
            t('gate fechado: 403 no módulo', cd.get(f'{M}/').status_code == 403)
            t('gate fechado: some de /modulos/', 'documentos' not in [m['chave'] for m in cd.get(f'{BASE}/modulos/').json()])
        inativo = novo('inativo', is_active=False)
        t('pode_entrar: inativo não entra', mod.pode_entrar(inativo) is False and mod.pode_entrar(dono) is True)

        print('\n== LISTA ==')
        r = cd.get(f'{M}/')
        js = r.json()
        ids = {d['id'] for d in js['documentos']}
        t('lista: 200', r.status_code == 200, r.content[:200])
        t('só os meus e liberados (oculto fora)', ids == {s1.pk, s2.pk}, ids)
        t('contadores', js['pendentes'] == 2 and js['assinados'] == 0, js)
        t('categorias presentes', [c['nome'] for c in js['categorias']] == ['ZZ Contratos App', 'ZZ Termos App'])
        t('filtro assinados vazio', cd.get(f'{M}/?situacao=assinados').json()['documentos'] == [])
        t('filtro pendentes', len(cd.get(f'{M}/?situacao=pendentes').json()['documentos']) == 2)

        print('\n== DETALHE E RECORTE ==')
        r = cd.get(f'{M}/{s1.pk}/')
        d = r.json()
        t('detalhe do meu: 200', r.status_code == 200 and d['titulo'] == 'ZZ Contrato'
          and d['descricao'] == 'Leia com atenção.' and d['categoria']['nome'] == 'ZZ Contratos App', r.content[:200])
        t('pode assinar o meu', d['pode_assinar'] is True and d['assinatura'] is None and d['pdf_assinado'] is None)
        t('documento de outra pessoa: 404', cd.get(f'{M}/{s_outro.pk}/').status_code == 404)
        t('documento não liberado: 404', cd.get(f'{M}/{s_oculto.pk}/').status_code == 404)
        t('id que não existe: 404', cd.get(f'{M}/999999999/').status_code == 404)
        r = ca.get(f'{M}/{s_outro.pk}/')
        t('ADMINISTRAÇÃO vê o de outra pessoa (como na web)', r.status_code == 200
          and r.json()['de_outra_pessoa']['id'] == outro.pk and r.json()['pode_assinar'] is False)
        t('ADMINISTRAÇÃO vê o não liberado (como na web)', ca.get(f'{M}/{s_oculto.pk}/').status_code == 200)
        t('lista do admin continua só a dele', ca.get(f'{M}/').json()['documentos'] == [])

        print('\n== PDF POR LINK ASSINADO ==')
        r = anon.get(d['pdf'])
        corpo = b''.join(r.streaming_content) if r.status_code == 200 else b''
        t('link abre o PDF sem token', r.status_code == 200 and corpo.startswith(b'%PDF'), r.status_code)
        t('o link do outro abre só o do outro', anon.get(co.get(f'{M}/{s_outro.pk}/').json()['pdf']).status_code == 200)
        t('link do dono para o do outro: 404', anon.get(forjar(dono, 'documentos', s_outro.pk)).status_code == 404)
        t('link para documento não liberado: 404', anon.get(forjar(dono, 'documentos', s_oculto.pk)).status_code == 404)
        t('link adulterado: 404', anon.get(d['pdf'][:-4] + 'xx/').status_code == 404)

        print('\n== ASSINAR ==')
        r = cd.post(f'{M}/{s_outro.pk}/assinar/', {'signature': PNG}, content_type='application/json')
        t('assinar o de outra pessoa: 404', r.status_code == 404, r.status_code)
        r = cd.post(f'{M}/{s_oculto.pk}/assinar/', {'signature': PNG}, content_type='application/json')
        t('assinar não liberado: 404', r.status_code == 404, r.status_code)
        r = ca.post(f'{M}/{s_outro.pk}/assinar/', {'signature': PNG}, content_type='application/json')
        t('admin não assina pelo outro: 403 (regra da web)', r.status_code == 403, r.status_code)
        r = cd.post(f'{M}/{s1.pk}/assinar/', {'signature': 'nada'}, content_type='application/json')
        t('formato errado: 400', r.status_code == 400 and 'inválido' in r.json()['erro'], r.content[:200])
        r = cd.post(f'{M}/{s1.pk}/assinar/', {'signature': PNG}, content_type='application/json',
                    HTTP_USER_AGENT='ZZ App')
        d = r.json()
        s1.refresh_from_db()
        t('assina: 200 e devolve o detalhe', r.status_code == 200 and d['assinado'] and not d['pode_assinar'],
          r.content[:200])
        t('gravou como a web (hora, hash, imagem, agente)', s1.signed_at and len(s1.signature_hash) == 64
          and s1.signature_image == PNG and s1.signature_user_agent == 'ZZ App')
        t('PDF assinado ficou no storage (em memória no teste)', bool(s1.signed_pdf and s1.signed_pdf.name)
          and memoria.exists(s1.signed_pdf.name))
        t('assinar de novo: 409', cd.post(f'{M}/{s1.pk}/assinar/', {'signature': PNG},
                                          content_type='application/json').status_code == 409)
        js = cd.get(f'{M}/').json()
        t('contadores mudaram', js['pendentes'] == 1 and js['assinados'] == 1)
        t('filtro assinados', [x['id'] for x in cd.get(f'{M}/?situacao=assinados').json()['documentos']] == [s1.pk])
        r = anon.get(d['pdf_assinado'])
        corpo = b''.join(r.streaming_content) if r.status_code == 200 else b''
        t('PDF assinado abre', r.status_code == 200 and corpo.startswith(b'%PDF'), r.status_code)
        t('PDF assinado de documento pendente: 404',
          anon.get(forjar(dono, 'documentos_assinado', s2.pk)).status_code == 404)

        print('\n== BLOQUEIO ==')
        with mock.patch('api_mobile.permissoes.bloqueio_atual',
                        return_value={'tipo': 'ferias', 'titulo': 'x', 'mensagem': 'x', 'caminho_web': '/'}):
            t('bloqueado no portal: 423', cd.get(f'{M}/').status_code == 423)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco (PDFs só em memória).')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
