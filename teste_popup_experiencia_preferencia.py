"""Preferência do popup "Alertas de Janela de Experiência": ligado por padrão, a pessoa desliga em Meu perfil.

(Quem recebe o painel está em teste_popup_experiencia.py.)

Roda numa transação desfeita no fim.
"""
import json
import os
import sys
from datetime import date, timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client

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


marcador = transaction.atomic()
marcador.__enter__()
try:
    ex = MigrationExecutor(connection)
    folhas = ex.loader.graph.leaf_nodes('users')
    if ex.migration_plan(folhas):
        ex.migrate(folhas)
    from users.models import Sector

    loja = Sector.objects.create(name='ZZ Popup Loja')

    def pessoa(username, **kw):
        return User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local', password='S3nha!teste',
                                        first_name='ZZ', last_name=username, **kw)

    chefe = pessoa('zzpe.super', hierarchy='SUPERADMIN')
    comum = pessoa('zzpe.comum', hierarchy='PADRAO', sector=loja)
    # Alguém na 1ª janela de experiência, para o popup ter o que mostrar.
    pessoa('zzpe.novato', sector=loja, has_experience_window=True, admission_date=date.today() - timedelta(days=40))
    ca, cc = Client(), Client()
    ca.force_login(chefe)
    cc.force_login(comum)

    print('== PADRÃO LIGADO ==')
    t('quem já tem continua com o popup ligado (padrão do campo)', User._meta.get_field('popup_janela_experiencia').default is True
      and User.objects.get(pk=chefe.pk).popup_janela_experiencia is True)
    t('a home mostra o popup', 'id="experience-window-modal"' in ca.get('/').content.decode())

    print('\n== MEU PERFIL ==')
    html = ca.get('/users/profile/').content.decode()
    t('quem recebe o popup vê a opção ligada', 'id="popupExperiencia"' in html and 'checked' in html.split('id="popupExperiencia"')[1][:200])
    t('quem não recebe não vê a opção', 'id="popupExperiencia"' not in cc.get('/users/profile/').content.decode())

    def salvar(corpo):
        return ca.post('/users/profile/popup-experiencia/', json.dumps(corpo), content_type='application/json')
    r = salvar({'ativo': False})
    t('desligar grava no cadastro', r.status_code == 200 and User.objects.get(pk=chefe.pk).popup_janela_experiencia is False)
    t('e a home deixa de mostrar o popup', 'id="experience-window-modal"' not in ca.get('/').content.decode())
    html = ca.get('/users/profile/').content.decode()
    t('o perfil mostra a opção desligada', 'checked' not in html.split('id="popupExperiencia"')[1][:200])
    salvar({'ativo': True})
    t('religar volta a mostrar', 'id="experience-window-modal"' in ca.get('/').content.decode())
    t('valor inválido é recusado', salvar({'ativo': 'sim'}).status_code == 400)
    t('só por POST', ca.get('/users/profile/popup-experiencia/').status_code == 405)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
