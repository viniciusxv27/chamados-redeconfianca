"""App mobile — módulo Facilitador (/api/mobile/v1/m/facilitador/).

Roda dentro de uma transação desfeita no fim. Os arquivos vão para um storage em
memória (o campo é trocado só durante o teste): nada sobe para o MinIO.
"""
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

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.db import transaction
from django.test import Client

from facilitador.models import Recurso
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/facilitador'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def forjar(user, tipo, obj_id):
    return f'{BASE}/arquivo/' + signing.dumps({'u': user.pk, 't': tipo, 'i': str(obj_id)},
                                               salt='api_mobile.arquivo', compress=True) + '/'


def caminho(url):
    return url.split('testserver', 1)[-1]


def corpo(resposta):
    return b''.join(resposta.streaming_content) if resposta.streaming else resposta.content


for nome in settings.CACHES:
    caches[nome].clear()

memoria = InMemoryStorage()
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(Recurso._meta.get_field('arquivo'), 'storage', memoria):
        setor = Sector.objects.create(name='ZZ Setor Facilitador App')

        def novo(apelido, hierarquia='PADRAO', **kw):
            return User.objects.create_user(
                username=f'zzfac.{apelido}', email=f'zzfac.{apelido}@exemplo-teste.local', password='S3nha!teste',
                first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia, **kw)

        ana = novo('ana')
        chefe = novo('chefe', 'SUPERADMIN')

        def arquivo(titulo, nome, conteudo, categoria='ZZ RH App', ativo=True, tipo_mime='application/pdf'):
            r = Recurso(titulo=titulo, categoria=categoria, descricao=f'{titulo} — explicação', ativo=ativo,
                        tipo=Recurso.TIPO_ARQUIVO, nome_original=nome, content_type=tipo_mime, tamanho=len(conteudo))
            r.arquivo.save(nome, ContentFile(conteudo), save=False)
            if r.e_html:
                r.token_pagina = 'zztokenpaginaapp'
            r.save()
            return r

        pdf = arquivo('ZZ Manual App', 'manual.pdf', b'%PDF-1.4 zz')
        oculto = arquivo('ZZ Oculto App', 'oculto.pdf', b'%PDF-1.4 oculto', ativo=False)
        pagina = arquivo('ZZ Página App', 'pagina.html', b'<script>alert(1)</script>', categoria='ZZ Sistemas App',
                         tipo_mime='text/html')
        svg = arquivo('ZZ Desenho App', 'desenho.svg', b'<svg onload="alert(1)"/>', categoria='ZZ Sistemas App',
                      tipo_mime='image/svg+xml')
        link_ = Recurso.objects.create(titulo='ZZ Sistema de vendas App', tipo=Recurso.TIPO_LINK, categoria='ZZ Sistemas App',
                                       url='https://exemplo-teste.local/vendas', descricao='Entre com seu login')

        def entrar(user):
            r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste',
                                                      'plataforma': 'ios', 'dispositivo': 'ZZ'},
                              content_type='application/json')
            return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

        ca, cc = entrar(ana), entrar(chefe)

        print('== ENTRADA ==')
        t('aparece em /modulos/ para todo logado (menu da web sem condição)',
          'facilitador' in [m['chave'] for m in ca.get(f'{BASE}/modulos/').json()])
        t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))
        from api_mobile.modulos import facilitador as mod
        with mock.patch.dict(mod.MODULO, {'pode_entrar': lambda u: False}):
            t('gate fechado: 403 no módulo', ca.get(f'{M}/').status_code == 403)
            t('gate fechado: some de /modulos/', 'facilitador' not in [m['chave'] for m in ca.get(f'{BASE}/modulos/').json()])
        inativo = novo('inativo', is_active=False)
        t('pode_entrar: inativo não entra', mod.pode_entrar(inativo) is False and mod.pode_entrar(ana) is True)

        print('\n== LISTA ==')
        r = ca.get(f'{M}/?busca=ZZ')
        js = r.json()
        titulos = {x['titulo'] for x in js['recursos']}
        t('lista: 200', r.status_code == 200, r.content[:200])
        t('colaborador não vê oculto', 'ZZ Oculto App' not in titulos and 'ZZ Manual App' in titulos, titulos)
        t('categorias da web', {'ZZ RH App', 'ZZ Sistemas App'} <= set(js['categorias']))
        t('não gerencia', js['gerencia'] is False)
        js_c = cc.get(f'{M}/?busca=ZZ').json()
        oc = next((x for x in js_c['recursos'] if x['id'] == oculto.pk), None)
        t('SUPERADMIN vê oculto marcado', oc is not None and oc['oculto'] is True and js_c['gerencia'] is True)
        r = ca.get(f'{M}/?busca=ZZ&tipo=link')
        t('filtro tipo link', [x['id'] for x in r.json()['recursos']] == [link_.pk])
        r = ca.get(f'{M}/?busca=ZZ&categoria=zz rh app')
        t('filtro categoria (sem caixa)', [x['id'] for x in r.json()['recursos']] == [pdf.pk], r.json()['recursos'])
        r = ca.get(f'{M}/?busca=seu login')
        t('busca na descrição', [x['id'] for x in r.json()['recursos']] == [link_.pk])

        print('\n== DETALHE ==')
        r = ca.get(f'{M}/{pdf.pk}/')
        t('detalhe: 200', r.status_code == 200 and r.json()['tem_arquivo'] is True and r.json()['nome_arquivo'] == 'manual.pdf', r.content[:200])
        t('acessos só para quem gerencia', r.json()['acessos'] is None and cc.get(f'{M}/{pdf.pk}/').json()['acessos'] == 0)
        t('oculto para colaborador: 404', ca.get(f'{M}/{oculto.pk}/').status_code == 404)
        t('oculto para SUPERADMIN: 200', cc.get(f'{M}/{oculto.pk}/').status_code == 200)
        t('inexistente: 404', ca.get(f'{M}/999999999/').status_code == 404)

        print('\n== ABRIR ==')
        r = ca.post(f'{M}/{link_.pk}/abrir/')
        link_.refresh_from_db()
        t('link: devolve a URL e conta o clique (view ir da web)', r.status_code == 200 and r.json() == {
            'tipo': 'link', 'url': 'https://exemplo-teste.local/vendas'} and link_.acessos == 1, r.content[:200])
        t('abrir oculto (colaborador): 404', ca.post(f'{M}/{oculto.pk}/abrir/').status_code == 404)

        r = ca.post(f'{M}/{pdf.pk}/abrir/')
        url = r.json().get('url', '')
        t('arquivo: link assinado', r.status_code == 200 and r.json()['tipo'] == 'arquivo' and '/api/mobile/v1/arquivo/' in url, r.content[:200])
        rb = Client().get(caminho(url))
        pdf.refresh_from_db()
        t('link assinado abre o PDF sem token', rb.status_code == 200 and corpo(rb) == b'%PDF-1.4 zz'
          and rb['Content-Type'] == 'application/pdf', (rb.status_code, rb.get('Content-Type')))
        t('download conta acesso (como baixar)', pdf.acessos == 1, pdf.acessos)

        r = ca.post(f'{M}/{pagina.pk}/abrir/')
        t('HTML: abre a página pública isolada', r.json().get('tipo') == 'pagina'
          and r.json()['url'].endswith('/facilitador/p/zztokenpaginaapp/'), r.json())
        r = ca.post(f'{M}/{pagina.pk}/abrir/', {'como': 'arquivo'}, content_type='application/json')
        rb = Client().get(caminho(r.json()['url']))
        t('HTML como arquivo: vai como download (nunca text/html)', rb.status_code == 200
          and 'html' not in rb['Content-Type'], rb.get('Content-Type'))
        rb = Client().get(caminho(cc.post(f'{M}/{svg.pk}/abrir/').json()['url']))
        t('SVG: nunca image/svg+xml', rb.status_code == 200 and 'svg' not in rb['Content-Type'], rb.get('Content-Type'))

        t('link forjado do oculto para colaborador: 404', Client().get(forjar(ana, 'facilitador', oculto.pk)).status_code == 404)
        t('link forjado do oculto para SUPERADMIN: 200', Client().get(forjar(chefe, 'facilitador', oculto.pk)).status_code == 200)
        t('link de item que é link (sem arquivo): 404', Client().get(forjar(ana, 'facilitador', link_.pk)).status_code == 404)
        oculto_depois = Recurso.objects.get(pk=pdf.pk)
        oculto_depois.ativo = False
        oculto_depois.save()
        t('item ocultado depois do link: link não abre mais', Client().get(caminho(url)).status_code == 404)
        t('assinatura adulterada: 404', Client().get(caminho(url)[:-3] + 'xx/').status_code == 404)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
