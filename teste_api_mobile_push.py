"""Push no celular (api_mobile/push.py): o sino do portal vira push do Expo.

Banco descartável (sqlite, schema direto dos models): a tabela api_mobile_pushmobile
só existe no Postgres compartilhado depois do `migrate api_mobile`. Nada sai para a
rede: o POST ao Expo é dublê em todos os casos, e web push/OneSignal também.
"""
import os
import pathlib
import sys
import tempfile
import threading
import time
from datetime import timedelta
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-push-mobile-'))
(PASTA / 'zz_push_mobile_settings.py').write_text(f"""
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
ONESIGNAL_APP_ID = ''
ONESIGNAL_REST_API_KEY = ''
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{n: {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-push-' + n}}
          for n in CACHES}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_push_mobile_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import DatabaseError, transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

from api_mobile import push  # noqa: E402
from api_mobile.models import PushMobile, TokenMobile  # noqa: E402
from core.models import Notification, NotificationMixin  # noqa: E402
from notifications.models import NotificationPreference, PushNotification, UserNotification  # noqa: E402
from notifications.services import notification_service  # noqa: E402
from users.models import Sector  # noqa: E402

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


class RespostaExpo:
    """Resposta do Expo: um ticket por mensagem, na ordem."""

    def __init__(self, tickets, status_code=200):
        self.status_code = status_code
        self._tickets = tickets

    def json(self):
        return {'data': self._tickets}


chamadas = []
mortos = set()  # tokens que o Expo diz não existir mais


def expo_falso(url, json=None, headers=None, timeout=None):
    chamadas.append({'url': url, 'mensagens': json, 'headers': headers, 'timeout': timeout})
    tickets = []
    for m in json:
        if m['to'] in mortos:
            tickets.append({'status': 'error', 'message': 'not registered',
                            'details': {'error': 'DeviceNotRegistered', 'expoPushToken': m['to']}})
        else:
            tickets.append({'status': 'ok', 'id': 'zz-ticket'})
    return RespostaExpo(tickets)


def mensagens():
    return [m for c in chamadas for m in c['mensagens']]


def tok(n):
    return f'ExponentPushToken[zzTokenDeTeste{n:04d}xx]'


# Nenhum envio externo, mesmo que algo escape: web push, OneSignal e o próprio requests.
dubles = [
    mock.patch('requests.post', side_effect=expo_falso),
    mock.patch('notifications.push_utils.webpush', side_effect=AssertionError('web push não pode sair')),
    mock.patch('api_mobile.push._script_de_teste', return_value=False),
]
for d in dubles:
    d.start()
push.EM_SEGUNDO_PLANO = False

setor = Sector.objects.create(name='ZZ Setor Push')


def novo(apelido, **kw):
    return User.objects.create_user(
        username=f'zzpush.{apelido}', email=f'zzpush.{apelido}@exemplo-teste.local', password='S3nha!teste',
        first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy='PADRAO', **kw)


ana, beto, caio = novo('ana'), novo('beto'), novo('caio')


def entrar(user):
    c = Client()
    r = c.post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste', 'plataforma': 'android',
                                        'dispositivo': 'ZZ Pixel'}, content_type='application/json')
    token = r.json()['token']
    return Client(HTTP_AUTHORIZATION=f'Bearer {token}'), token


def registrar(cliente, token_expo):
    return cliente.post(f'{BASE}/push/registrar/', {'token': token_expo, 'plataforma': 'android'},
                        content_type='application/json')


print('== REGISTRAR / DESREGISTRAR ==')
ca, chave_a = entrar(ana)
t('sem login: 401', Client().post(f'{BASE}/push/registrar/', {'token': tok(1)},
                                   content_type='application/json').status_code == 401)
r = registrar(ca, 'qualquer-coisa')
t('token fora do formato do Expo: 400', r.status_code == 400 and not PushMobile.objects.exists(), r.content[:200])
r = registrar(ca, tok(1))
sessao_a = TokenMobile.objects.get(user=ana)
t('registrar guarda o token preso à sessão do aparelho', r.status_code == 200
  and PushMobile.objects.filter(sessao=sessao_a, token_expo=tok(1)).exists(), r.content[:200])
r = registrar(ca, tok(2))
t('registrar de novo troca o token da mesma sessão (não duplica)',
  list(PushMobile.objects.filter(sessao=sessao_a).values_list('token_expo', flat=True)) == [tok(2)])
cb, _ = entrar(beto)
registrar(cb, tok(2))
t('outra pessoa entra no mesmo aparelho: o token muda de dono',
  PushMobile.objects.get(token_expo=tok(2)).sessao.user_id == beto.pk
  and not PushMobile.objects.filter(sessao=sessao_a).exists())
registrar(ca, tok(1))
r = ca.post(f'{BASE}/push/desregistrar/')
t('desregistrar apaga o token da sessão', r.status_code == 200 and not PushMobile.objects.filter(sessao=sessao_a).exists())
registrar(ca, tok(1))

print('\n== NOTIFICAÇÃO DO PORTAL → PUSH ==')
chamadas.clear()
n = Notification.objects.create(user=ana, title='ZZ <b>Documento</b> para assinar', message='<p>Assine o termo</p>',
                                notification_type='SYSTEM', related_url='/documentos/7/')
t('core.Notification.create vira um POST ao Expo', len(chamadas) == 1
  and chamadas[0]['url'] == 'https://exp.host/--/api/v2/push/send', chamadas)
m = mensagens()[0] if mensagens() else {}
t('a mensagem vai para o token da pessoa', m.get('to') == tok(1), m)
t('título e corpo sem HTML', m.get('title') == 'ZZ Documento para assinar' and m.get('body') == 'Assine o termo', m)
t('o link do portal vai no data (o app abre a tela certa)', m.get('data') == {'url': '/documentos/7/', 'id': f'c-{n.pk}'}, m)
t('canal Android, som e prioridade', m.get('channelId') == 'default' and m.get('sound') == 'default'
  and m.get('priority') == 'high', m)

chamadas.clear()
Notification.objects.create(user=caio, title='ZZ sem app', message='x', notification_type='SYSTEM')
t('quem não entrou pelo app não gera POST nenhum', chamadas == [])

chamadas.clear()
NotificationMixin.create_notifications_for_users([ana, beto, caio], 'ZZ Novo arquivo', 'Arquivo novo', 'FILE',
                                                 related_url='/files/')
t('bulk_create (NotificationMixin) vira um POST só com os dois aparelhos',
  len(chamadas) == 1 and sorted(m['to'] for m in mensagens()) == [tok(1), tok(2)], chamadas)

chamadas.clear()
notification_service.send_notification(ana, 'ZZ Chamado atribuído', 'Você recebeu o chamado #9',
                                       notification_type='ticket_assigned', channels=['in_app'],
                                       action_url='/tickets/9/')
un = UserNotification.objects.filter(user=ana, notification__title='ZZ Chamado atribuído').first()
t('sino novo (notifications.services, bulk_create) também vira push',
  un is not None and any(m['to'] == tok(1) and m['data'] == {'url': '/tickets/9/', 'id': f'p-{un.pk}'}
                         for m in mensagens()), mensagens())

chamadas.clear()
aviso = PushNotification.objects.create(title='ZZ Aviso geral', message='Leia', created_by=beto,
                                        action_url='https://chamados.redeconfianca.com.br/communications/3/')
aviso.target_users.add(ana)
with mock.patch('notifications.push_utils.push_service.send_to_users', return_value={'message': 'dublê'}):
    aviso.send_notification()
t('PushNotification.send_notification (bulk_create do sino) vira push com a URL completa',
  [m['data']['url'] for m in mensagens()] == ['https://chamados.redeconfianca.com.br/communications/3/'], mensagens())

chamadas.clear()
outro = PushNotification.objects.create(title='ZZ Chat', message='Nova mensagem', created_by=ana)
UserNotification.objects.create(notification=outro, user=beto)
t('UserNotification.objects.create (chat de projetos, rotina) vira push',
  [m['to'] for m in mensagens()] == [tok(2)], mensagens())

print('\n== PREFERÊNCIAS ==')
pref = NotificationPreference.objects.create(user=ana, push_enabled=False)
chamadas.clear()
Notification.objects.create(user=ana, title='ZZ push off', message='x', notification_type='SYSTEM')
t('push desligado nas preferências: não envia', chamadas == [])
pref.push_enabled, pref.in_app_enabled = True, False
pref.save()
Notification.objects.create(user=ana, title='ZZ sino off', message='x', notification_type='SYSTEM')
t('sino (in_app) desligado: não envia', chamadas == [])
pref.in_app_enabled, pref.communication_new = True, False
pref.save()
Notification.objects.create(user=ana, title='ZZ comunicado', message='x', notification_type='COMMUNICATION')
t('tipo desligado (comunicados): não envia', chamadas == [])
Notification.objects.create(user=ana, title='ZZ sistema', message='x', notification_type='SYSTEM')
t('o que ela não desligou continua chegando', len(mensagens()) == 1)
pref.communication_new = True
pref.quiet_hours_enabled = True
agora = timezone.localtime()
pref.quiet_hours_start = (agora - timedelta(minutes=30)).time()
pref.quiet_hours_end = (agora + timedelta(minutes=30)).time()
pref.save()
chamadas.clear()
Notification.objects.create(user=ana, title='ZZ silêncio', message='x', notification_type='SYSTEM')
t('horário silencioso: não envia', chamadas == [])
pref.delete()

print('\n== TOKEN INVÁLIDO, SESSÃO ENCERRADA, FALHAS ==')
mortos.add(tok(2))
chamadas.clear()
NotificationMixin.create_notifications_for_users([ana, beto], 'ZZ Dois', 'x', 'SYSTEM')
t('DeviceNotRegistered apaga o token do aparelho', not PushMobile.objects.filter(token_expo=tok(2)).exists()
  and PushMobile.objects.filter(token_expo=tok(1)).exists())
chamadas.clear()
Notification.objects.create(user=beto, title='ZZ depois', message='x', notification_type='SYSTEM')
t('e o aparelho apagado não recebe mais', chamadas == [])
mortos.clear()

chamadas.clear()
with mock.patch('requests.post', side_effect=ConnectionError('Expo fora do ar')):
    try:
        n = Notification.objects.create(user=ana, title='ZZ Expo fora', message='x', notification_type='SYSTEM')
        criou = Notification.objects.filter(pk=n.pk).exists()
    except Exception as erro:  # noqa: BLE001
        criou = False
        print('   ', repr(erro))
t('Expo fora do ar: a notificação é criada e nada quebra', criou)


class RespostaHtml:
    status_code = 502

    def json(self):
        raise ValueError('não é JSON')


with mock.patch('requests.post', return_value=RespostaHtml()):
    try:
        Notification.objects.create(user=ana, title='ZZ 502', message='x', notification_type='SYSTEM')
        criou = True
    except Exception as erro:  # noqa: BLE001
        criou = False
        print('   ', repr(erro))
t('Expo devolvendo HTML/502: nada quebra', criou and PushMobile.objects.filter(token_expo=tok(1)).exists())

with mock.patch.object(PushMobile.objects, 'filter', side_effect=DatabaseError('relation "api_mobile_pushmobile" does not exist')):
    try:
        with transaction.atomic():
            Notification.objects.create(user=ana, title='ZZ antes do migrate', message='x', notification_type='SYSTEM')
            Notification.objects.filter(user=ana).count()  # a transação de quem criou continua usável
        criou = Notification.objects.filter(title='ZZ antes do migrate').exists()
    except Exception as erro:  # noqa: BLE001
        criou = False
        print('   ', repr(erro))
t('antes do migrate (tabela do push não existe): a notificação sai igual', criou)

chamadas.clear()
with mock.patch('api_mobile.push._script_de_teste', return_value=True):
    Notification.objects.create(user=ana, title='ZZ trava', message='x', notification_type='SYSTEM')
t('script teste_*.py não envia nada (mesma trava do WhatsApp/e-mail)', chamadas == [])

chamadas.clear()
with transaction.atomic():
    Notification.objects.create(user=ana, title='ZZ na transação', message='x', notification_type='SYSTEM')
    antes_do_commit = len(chamadas)
t('dentro da transação o push espera o commit', antes_do_commit == 0 and len(chamadas) == 1)
chamadas.clear()
try:
    with transaction.atomic():
        Notification.objects.create(user=ana, title='ZZ desfeita', message='x', notification_type='SYSTEM')
        raise RuntimeError('desfaz')
except RuntimeError:
    pass
t('notificação desfeita (rollback) não avisa', chamadas == [])

chamadas.clear()
muitos = [novo(f'lote{i}') for i in range(120)]
sessoes = [TokenMobile.emitir(u, plataforma='android')[0] for u in muitos]
PushMobile.objects.bulk_create([PushMobile(sessao=s, token_expo=tok(100 + i)) for i, s in enumerate(sessoes)])
NotificationMixin.create_notifications_for_users(muitos, 'ZZ Para muitos', 'x', 'SYSTEM')
t('mais de 100 mensagens vão em lotes de 100', [len(c['mensagens']) for c in chamadas] == [100, 20],
  [len(c['mensagens']) for c in chamadas])

chamadas.clear()
TokenMobile.objects.filter(pk=sessoes[0].pk).update(ultimo_uso=timezone.now() - timedelta(days=61))
muitos[1].is_active = False
muitos[1].save()
Notification.objects.bulk_create([Notification(user=u, title='ZZ filtro', message='x', notification_type='SYSTEM')
                                  for u in muitos[:3]])
t('sessão expirada (60 dias parada) e usuário desativado não recebem', [m['to'] for m in mensagens()] == [tok(102)],
  [m['to'] for m in mensagens()])

r = ca.post(f'{BASE}/auth/logout/')
chamadas.clear()
Notification.objects.create(user=ana, title='ZZ depois de sair', message='x', notification_type='SYSTEM')
t('logout apaga o token e o aparelho não recebe mais', r.status_code == 200 and chamadas == []
  and not PushMobile.objects.filter(token_expo=tok(1)).exists())

print('\n== EM SEGUNDO PLANO (como no portal) ==')
cd, _ = entrar(caio)
registrar(cd, tok(9))
push.EM_SEGUNDO_PLANO = True
chamadas.clear()


def expo_lento(*a, **kw):
    time.sleep(1.5)
    return expo_falso(*a, **kw)


with mock.patch('requests.post', side_effect=expo_lento):
    inicio = time.monotonic()
    Notification.objects.create(user=caio, title='ZZ fila', message='x', notification_type='SYSTEM')
    NotificationMixin.create_notifications_for_users([caio], 'ZZ fila 2', 'x', 'SYSTEM')
    gasto = time.monotonic() - inicio
    t('a requisição não espera o Expo (fila + thread)', gasto < 0.5 and chamadas == [], f'{gasto:.2f}s')
    limite = time.monotonic() + 15
    while push._fila.unfinished_tasks and time.monotonic() < limite:
        time.sleep(0.05)
t('a thread push-mobile envia depois', sorted(m['title'] for m in mensagens()) == ['ZZ fila', 'ZZ fila 2']
  and any(th.name == 'push-mobile' for th in threading.enumerate()), mensagens())
push.EM_SEGUNDO_PLANO = False

for d in dubles:
    d.stop()

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
