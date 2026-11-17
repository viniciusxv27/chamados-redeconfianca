"""Base comum dos módulos no app: catálogo filtrado, permissão por módulo e link assinado de arquivo.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
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
from django.db import transaction
from django.http import Http404
from django.test import Client
from django.urls import path
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from api_mobile import arquivos
from api_mobile.autenticacao import TokenMobileAuthentication
from api_mobile.modulos import CHAVES, modulos_do_usuario, permissao
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


for nome in settings.CACHES:
    caches[nome].clear()

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Modulos App')
    ana = User.objects.create_user(username='zzmod.ana', email='zzmod.ana@exemplo-teste.local', password='S3nha!teste',
                                   first_name='ZZAna', last_name='Teste', sector=setor, hierarchy='PADRAO')
    beto = User.objects.create_user(username='zzmod.beto', email='zzmod.beto@exemplo-teste.local', password='S3nha!teste',
                                    first_name='ZZBeto', last_name='Teste', sector=setor, hierarchy='PADRAO')

    def entrar(u):
        r = Client().post(f'{BASE}/auth/login/', {'email': u.email, 'senha': 'S3nha!teste'}, content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

    ca, cb = entrar(ana), entrar(beto)

    print('== CATÁLOGO ==')
    r = ca.get(f'{BASE}/modulos/')
    t('responde a lista', r.status_code == 200 and isinstance(r.json(), list), r.content[:200])
    t('todo módulo listado tem chave conhecida e rota /modulo/<chave>',
      all(m['chave'] in CHAVES and m['rota'] == f"/modulo/{m['chave']}" for m in r.json()))
    t('sem login: 401/403', Client().get(f'{BASE}/modulos/').status_code in (401, 403))

    def so_libera(chaves):
        """Troca o gate de cada módulo por um que só abre as chaves pedidas para a Ana."""
        from api_mobile.modulos import carregar
        originais = {c: carregar(c)['pode_entrar'] for c in CHAVES}
        for c in CHAVES:
            carregar(c)['pode_entrar'] = (lambda c: lambda u: c in chaves and u.pk == ana.pk)(c)
        return originais

    def restaurar(originais):
        from api_mobile.modulos import carregar
        for c, f in originais.items():
            carregar(c)['pode_entrar'] = f

    originais = so_libera({'ponto'})
    try:
        t('mostra só o que o gate abre', [m['chave'] for m in modulos_do_usuario(ana)] == ['ponto'])
        t('para quem o gate fecha, nada', modulos_do_usuario(beto) == [])
    finally:
        restaurar(originais)

    from api_mobile.modulos import carregar
    original = carregar('ponto')['pode_entrar']
    carregar('ponto')['pode_entrar'] = lambda u: (_ for _ in ()).throw(RuntimeError('quebrou'))
    try:
        t('gate que explode conta como fechado', all(m['chave'] != 'ponto' for m in modulos_do_usuario(ana)))
    finally:
        carregar('ponto')['pode_entrar'] = original

    print('\n== PERMISSÃO POR MÓDULO ==')

    @api_view(['GET'])
    @authentication_classes([TokenMobileAuthentication])
    @permission_classes([permissao('ponto')])
    def sonda(request):
        return Response({'ok': True})

    from django.urls import clear_url_caches, include
    import redeconfianca.urls as raiz
    raiz.urlpatterns.insert(0, path('zz-sonda-modulo/', sonda))
    clear_url_caches()
    originais = so_libera({'ponto'})
    try:
        t('com acesso: 200', ca.get('/zz-sonda-modulo/').status_code == 200)
        r = cb.get('/zz-sonda-modulo/')
        t('sem acesso: 403 com o nome do módulo', r.status_code == 403 and 'Ponto' in r.json().get('detail', ''), r.content[:200])
        t('sem token: 401/403', Client().get('/zz-sonda-modulo/').status_code in (401, 403))
        with mock.patch('api_mobile.permissoes.bloqueio_atual',
                        return_value={'tipo': 'ferias', 'titulo': 'Férias', 'mensagem': 'Férias', 'caminho_web': '/'}):
            t('bloqueio do portal vale antes do módulo (423)', ca.get('/zz-sonda-modulo/').status_code == 423)
    finally:
        restaurar(originais)
        raiz.urlpatterns.pop(0)
        clear_url_caches()

    print('\n== LINK ASSINADO DE ARQUIVO ==')
    conteudo = ContentFile(b'%PDF-1.4 zz', name='zz.pdf')

    @arquivos.registrar('zz-teste')
    def _resolver(user, obj_id):
        if user.pk != ana.pk or obj_id != '7':
            raise Http404
        return conteudo, 'zz.pdf', 'application/pdf'

    class Req:
        user = ana
        META = {}

        @staticmethod
        def build_absolute_uri(caminho):
            return f'http://testserver{caminho}'

    url = arquivos.link(Req, 'zz-teste', 7)
    caminho = url.replace('http://testserver', '')
    r = Client().get(caminho)
    t('o dono abre o arquivo sem token (só pelo link)', r.status_code == 200 and b''.join(r.streaming_content) == b'%PDF-1.4 zz',
      r.status_code)
    t('não fica em cache', r.get('Cache-Control') == 'private, no-store')
    adulterado = caminho[:-3] + ('A' if caminho[-3] != 'A' else 'B') + caminho[-2:]
    t('link adulterado: 404', Client().get(adulterado).status_code == 404)
    Req.user = beto
    alheio = arquivos.link(Req, 'zz-teste', 7).replace('http://testserver', '')
    t('link de outra pessoa para o mesmo id: o resolvedor recusa (404)', Client().get(alheio).status_code == 404)
    with mock.patch('api_mobile.arquivos.VALIDADE_SEGUNDOS', -1):
        t('link vencido: 404', Client().get(caminho).status_code == 404)
    ana.is_active = False
    ana.save(update_fields=['is_active'])
    t('dono desativado: 404', Client().get(caminho).status_code == 404)
    Req.META = {'HTTP_X_FORWARDED_PROTO': 'https'}
    t('atrás do proxy HTTPS o link sai em https', arquivos.link(Req, 'zz-teste', 7).startswith('https://testserver/'))
    arquivos.RESOLVEDORES.pop('zz-teste', None)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
