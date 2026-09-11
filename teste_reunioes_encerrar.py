"""Reuniões: "Encerrar reunião" no detalhe e na sala marca a reunião como finalizada.

Pedido: botão de encerrar em /reunioes/{id}/ e em /reunioes/{id}/sala/. Só quem
edita a reunião (organizador ou SUPERADMIN) encerra; convidado não vê o botão e o
servidor recusa. Reunião cancelada não vira encerrada, e a que já acabou não
oferece mais encerrar nem cancelar.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from reunioes.models import ParticipanteReuniao, Reuniao

User = get_user_model()
NODE = shutil.which('node')
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def novo(username, **kw):
    return User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local',
                                    password='S3nha!teste', **kw)


def sintaxe_ok(codigo):
    if not NODE:
        return True, 'node ausente'
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(codigo)
        caminho = fh.name
    try:
        r = subprocess.run([NODE, '--check', caminho], capture_output=True, text=True)
        return r.returncode == 0, r.stderr[-800:]
    finally:
        os.unlink(caminho)


XHR = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

marcador = transaction.atomic()
marcador.__enter__()
try:
    org = novo('zzre.org', first_name='ZZ', last_name='Organizadora')
    conv = novo('zzre.conv', first_name='ZZ', last_name='Convidado')
    chefe = novo('zzre.chefe', first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN')

    agora = timezone.now()
    reuniao = Reuniao.objects.create(titulo='ZZRE Alinhamento', organizador=org, inicio=agora)
    ParticipanteReuniao.objects.create(reuniao=reuniao, user=conv)

    c_org, c_conv, c_chefe = Client(), Client(), Client()
    c_org.force_login(org)
    c_conv.force_login(conv)
    c_chefe.force_login(chefe)

    DETALHE = f'/reunioes/{reuniao.id}/'
    SALA = f'/reunioes/{reuniao.id}/sala/'
    ENCERRAR = f'/reunioes/{reuniao.id}/encerrar/'

    print('== DETALHE ==')
    html = c_org.get(DETALHE).content.decode()
    t('a organizadora vê "Encerrar reunião"', 'Encerrar reunião' in html and f'action="{ENCERRAR}"' in html)
    t('com confirmação antes', "confirm('Encerrar a reunião e marcar como finalizada?')" in html)
    t('o convidado não vê o botão', 'Encerrar reunião' not in c_conv.get(DETALHE).content.decode())
    t('o SUPERADMIN vê', 'Encerrar reunião' in c_chefe.get(DETALHE).content.decode())

    print('\n== SALA ==')
    r = c_org.get(SALA)
    html = r.content.decode()
    t('a sala abre (200)', r.status_code == 200, r.status_code)
    t('a organizadora tem o botão na sala', 'id="rn-encerrar"' in html)
    t('que chama o encerrar da reunião', ENCERRAR in html)
    t('e derruba a chamada de todos ao encerrar', "api.executeCommand('endConference')" in html)
    blocos = [b for b in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, flags=re.S)
              if 'JitsiMeetExternalAPI(servidor' in b]
    t('um script da sala', len(blocos) == 1, len(blocos))
    if blocos:
        valido, erro = sintaxe_ok(blocos[0])
        t('o JavaScript da sala tem sintaxe válida (node --check)', valido, erro)
    html = c_conv.get(SALA).content.decode()
    t('o convidado entra, mas sem o botão', 'id="rn-encerrar"' not in html and 'JitsiMeetExternalAPI(servidor' in html)
    reuniao.refresh_from_db()
    t('entrar na sala deixa a reunião em andamento', reuniao.status == Reuniao.EM_ANDAMENTO, reuniao.status)

    print('\n== QUEM ENCERRA ==')
    r = c_conv.post(ENCERRAR, **XHR)
    reuniao.refresh_from_db()
    t('o convidado não encerra pela sala (403)', r.status_code == 403 and reuniao.status == Reuniao.EM_ANDAMENTO,
      r.status_code)
    r = c_conv.post(ENCERRAR, follow=True)
    reuniao.refresh_from_db()
    t('nem pelo formulário: volta com o aviso', reuniao.status == Reuniao.EM_ANDAMENTO
      and 'Só quem organizou a reunião pode encerrá-la.' in r.content.decode())
    t('GET não encerra (405)', c_org.get(ENCERRAR).status_code == 405)

    r = c_org.post(ENCERRAR, follow=True)
    reuniao.refresh_from_db()
    t('pelo detalhe: a reunião fica encerrada', reuniao.status == Reuniao.ENCERRADA, reuniao.status)
    t('e volta para o detalhe com o aviso', bool(r.redirect_chain) and r.redirect_chain[-1][0] == DETALHE
      and 'marcada como finalizada' in r.content.decode(), r.redirect_chain)
    html = c_org.get(DETALHE).content.decode()
    t('encerrada, o detalhe não oferece mais encerrar nem cancelar',
      'Encerrar reunião' not in html and 'Cancelar reunião' not in html)
    t('e mostra o status "Encerrada"', 'Encerrada' in html)
    html = c_org.get(SALA).content.decode()
    reuniao.refresh_from_db()
    t('a sala da reunião encerrada não mostra o botão', 'id="rn-encerrar"' not in html)
    t('e entrar nela não reabre a reunião', reuniao.status == Reuniao.ENCERRADA, reuniao.status)

    retro = Reuniao.objects.create(titulo='ZZRE Retro', organizador=org, inicio=agora, status=Reuniao.EM_ANDAMENTO)
    r = c_chefe.post(f'/reunioes/{retro.id}/encerrar/', **XHR)
    retro.refresh_from_db()
    t('o SUPERADMIN encerra pela sala (JSON)', r.status_code == 200 and r.json().get('ok') is True
      and retro.status == Reuniao.ENCERRADA, r.content[:200])
    r = c_org.post(f'/reunioes/{retro.id}/encerrar/', **XHR)
    t('encerrar de novo não quebra', r.status_code == 200 and r.json().get('ok') is True)

    cancelada = Reuniao.objects.create(titulo='ZZRE Cancelada', organizador=org, inicio=agora,
                                       status=Reuniao.CANCELADA)
    r = c_org.post(f'/reunioes/{cancelada.id}/encerrar/', **XHR)
    cancelada.refresh_from_db()
    t('reunião cancelada não vira encerrada (409)', r.status_code == 409
      and cancelada.status == Reuniao.CANCELADA, r.status_code)
    t('a cancelada não oferece encerrar', 'Encerrar reunião' not in c_org.get(f'/reunioes/{cancelada.id}/').content.decode())

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
