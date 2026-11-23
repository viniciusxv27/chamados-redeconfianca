"""Nenhum popup nem trava prende o SUPERADMIN (23/11/2026).

SUPERADMIN = is_superuser OU hierarquia SUPERADMIN (quem só tem a hierarquia era preso
pelo bloqueio de férias/jornada, pelo ajuste de pré-cadastro e pelos popups bloqueantes
— comunicado obrigatório, pesquisa, login do Acess).

Cada trava é forçada (dublês) e conferida lado a lado: o colaborador comum continua
travado; o SUPERADMIN passa. Postgres de dev numa transação desfeita; cache em memória.
"""
import os
import sys
from types import SimpleNamespace
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.cache import caches  # noqa: E402
from django.db import transaction  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.test import RequestFactory  # noqa: E402

import tangerino.middleware as tmw  # noqa: E402
from cursos import permissions as cursos_perm  # noqa: E402
from portal_popups import context_processors as pp  # noqa: E402
from users.pre_registration_middleware import PreRegistrationAdjustmentMiddleware  # noqa: E402

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


rf = RequestFactory()
passou = HttpResponse('passou')


def pedido(user, caminho='/'):
    from django.contrib.messages.storage.fallback import FallbackStorage
    r = rf.get(caminho)
    r.user = user
    r.resolver_match = None
    r.session = {}
    r._messages = FallbackStorage(r)
    return r


def travou(resposta):
    return getattr(resposta, 'status_code', 200) in (301, 302) or (resposta is not None and resposta is not passou)


marcador = transaction.atomic()
marcador.__enter__()
try:
    sa = User.objects.create_user(username='zzsa.hier', email='zzsa.hier@exemplo-teste.local', password='S3nha!teste',
                                  first_name='ZZ', last_name='Superadmin', hierarchy='SUPERADMIN', is_superuser=False)
    su = User.objects.create_user(username='zzsa.su', email='zzsa.su@exemplo-teste.local', password='S3nha!teste',
                                  first_name='ZZ', last_name='Superuser', hierarchy='PADRAO', is_superuser=True)
    comum = User.objects.create_user(username='zzsa.comum', email='zzsa.comum@exemplo-teste.local', password='S3nha!teste',
                                     first_name='ZZ', last_name='Comum', hierarchy='PADRAO')
    nao_e_rh = mock.patch.object(User, 'can_manage_rh', lambda self: False, create=True)
    nao_e_rh.start()

    print('== FÉRIAS ==')
    cfg = SimpleNamespace(bloquear_navegacao_ferias=True, libera_ferias=lambda u: True)
    with mock.patch('tangerino.models.ConfiguracaoTangerino.get', return_value=cfg), \
            mock.patch.object(tmw, 'esta_de_ferias', return_value=True), \
            mock.patch.object(tmw, 'disparar_se_esta_na_hora'), mock.patch.object(tmw, 'disparar_analise_se_esta_na_hora'):
        mw = tmw.BloqueioFeriasMiddleware(lambda r: passou)
        t('colaborador de férias continua travado', travou(mw(pedido(comum))))
        t('SUPERADMIN (só a hierarquia) de férias passa', mw(pedido(sa)) is passou)
        t('superuser de férias passa', mw(pedido(su)) is passou)

    print('\n== JORNADA ==')
    caches['local'].clear()
    with mock.patch.object(tmw, 'decidir_bloqueio', return_value={'motivo': 'entrada'}):
        mw = tmw.BloqueioJornadaMiddleware(lambda r: passou)
        t('colaborador sem bater o ponto continua travado', travou(mw(pedido(comum, '/tickets/'))))
        t('SUPERADMIN sem bater o ponto passa', mw(pedido(sa, '/tickets/')) is passou)

    print('\n== AJUSTE DE PRÉ-CADASTRO ==')
    with mock.patch.object(User, 'needs_pre_registration_adjustment', lambda self: True, create=True):
        mw = PreRegistrationAdjustmentMiddleware(lambda r: passou)
        t('colaborador com pré-cadastro reprovado é levado ao ajuste', travou(mw.process_view(pedido(comum), None, (), {})))
        t('SUPERADMIN não é levado', mw.process_view(pedido(sa), None, (), {}) is None)

    print('\n== POPUPS DO PORTAL (comunicado obrigatório etc.) ==')
    bloqueante = SimpleNamespace(is_within_window=lambda: True, applies_to=lambda u: True,
                                 is_completed_by=lambda u: False, is_blocking_now=lambda: True, action_url='')
    lista = mock.MagicMock()
    lista.order_by.return_value = [bloqueante]
    with mock.patch('portal_popups.models.PortalPopup.objects.filter', return_value=lista), \
            mock.patch.object(pp, '_deve_bater_ponto', return_value=False):
        t('colaborador recebe o popup bloqueante', pp.portal_popup_gate(pedido(comum, '/tickets/'))['portal_popup_blocking'] is True)
        ctx = pp.portal_popup_gate(pedido(sa, '/tickets/'))
        t('SUPERADMIN não recebe popup nenhum', ctx['portal_popup'] is None and ctx['portal_popup_blocking'] is False, ctx)
        t('para as outras travas, o colaborador tem popup bloqueante pendente', pp.tem_popup_bloqueante_pendente(comum))
        t('o SUPERADMIN não', not pp.tem_popup_bloqueante_pendente(sa) and not pp.tem_popup_bloqueante_pendente(su))

    print('\n== AVISOS NÃO MOSTRADOS (botão do SUPERADMIN) ==')
    popup_fake = SimpleNamespace(title='ZZ Pesquisa de clima', message='Responda a pesquisa', action_url='/clima/',
                                 action_label='Responder', is_within_window=lambda: True, applies_to=lambda u: True,
                                 is_completed_by=lambda u: False, is_blocking_now=lambda: True)
    lista2 = mock.MagicMock()
    lista2.order_by.return_value = [popup_fake]
    with mock.patch('portal_popups.models.PortalPopup.objects.filter', return_value=lista2), \
            mock.patch('acess.popup_login.campanha_pendente', return_value=None), \
            mock.patch.object(pp, '_deve_bater_ponto', return_value=False):
        av = pp.avisos_ocultos_do_superadmin(sa)
        t('o SUPERADMIN vê o popup que deixou de aparecer', len(av) == 1 and av[0]['titulo'] == 'ZZ Pesquisa de clima'
          and av[0]['bloqueante'] and av[0]['url'] == '/clima/', av)
        t('colaborador comum não tem lista (ele recebe o popup)', pp.avisos_ocultos_do_superadmin(comum) == [])
        home = SimpleNamespace(url_name='home', namespace='')
        r = pedido(sa, '/'); r.resolver_match = home
        t('na home sem pedir, nada de popup', pp.portal_popup_gate(r)['portal_popup'] is None)
        r = rf.get('/?ver_avisos=1'); r.user = sa; r.resolver_match = home
        ctx = pp.portal_popup_gate(r)
        t('"Mostrar como popup" abre o popup, sem travar', ctx['portal_popup'] is popup_fake and ctx['portal_popup_blocking'] is False, ctx)
        r = rf.get('/tickets/?ver_avisos=1'); r.user = sa; r.resolver_match = SimpleNamespace(url_name='tickets_list', namespace='')
        t('fora da home o ?ver_avisos não abre nada', pp.portal_popup_gate(r)['portal_popup'] is None)

    print('\n== HOME → /cursos/ ==')
    with mock.patch.object(cursos_perm, 'pode_ver', return_value=True), \
            mock.patch.object(cursos_perm, 'pendencias', return_value=['curso']):
        cfg_c = SimpleNamespace()
        t('colaborador PADRÃO com curso pendente vai para /cursos/', cursos_perm.deve_ir_para_cursos(comum, cfg_c))
        t('superuser com hierarquia PADRÃO não vai', not cursos_perm.deve_ir_para_cursos(su, cfg_c))
        t('SUPERADMIN não vai', not cursos_perm.deve_ir_para_cursos(sa, cfg_c))

    print('\n== CURSO E TRILHA (já liberavam; confere que continua) ==')
    from cursos.middleware import BloqueioCursoMiddleware
    from knowledge_trails.bloqueio import trilha_que_trava
    with mock.patch('cursos.cobranca.disparar_se_esta_na_hora'):
        t('bloqueio de curso deixa o SUPERADMIN passar', BloqueioCursoMiddleware(lambda r: passou)(pedido(sa, '/tickets/')) is passou)
    t('trilha obrigatória não tranca o SUPERADMIN', trilha_que_trava(sa) is None)
    nao_e_rh.stop()
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste ficou no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
