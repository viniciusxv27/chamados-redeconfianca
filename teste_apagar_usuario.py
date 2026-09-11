"""/users/manage/users/: botão para apagar o usuário.

Só o SUPERADMIN apaga. Antes de confirmar, a tela mostra o que vai junto (em
cascata) e o que impede (registros protegidos, como cartão sob responsabilidade
da pessoa) — nesse caso, a saída é desativar. Confirmar exige digitar o usuário.
Ninguém apaga a si mesmo, e superusuário só é apagado por superusuário.

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

from cartoes.models import Cartao
from core.models import SystemLog, TaskActivity

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
    kw.setdefault('first_name', 'ZZ')
    kw.setdefault('last_name', username.split('.')[-1].title())
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


marcador = transaction.atomic()
marcador.__enter__()
try:
    chefe = novo('zzau.chefe', hierarchy='SUPERADMIN')
    admin = novo('zzau.admin', hierarchy='ADMIN')
    root = novo('zzau.root', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    alvo = novo('zzau.alvo', last_name='Apagavel')
    com_cartao = novo('zzau.cartao')

    tarefa = TaskActivity.objects.create(title='ZZ tarefa do alvo', description='x',
                                         assigned_to=alvo, created_by=chefe)
    Cartao.objects.create(apelido='ZZ cartão', first4='1234', last4='5678', responsavel=com_cartao,
                          validade_mes=12, validade_ano=2030,
                          bandeira=Cartao._meta.get_field('bandeira').choices[0][0])

    c_chefe, c_admin, c_root = Client(), Client(), Client()
    c_chefe.force_login(chefe)
    c_admin.force_login(admin)
    c_root.force_login(root)

    def url(u):
        return f'/users/manage/users/{u.id}/delete/'

    print('== A LISTA ==')
    r = c_chefe.get('/users/manage/users/')
    html = r.content.decode()
    t('abre (200)', r.status_code == 200, r.status_code)
    t('o SUPERADMIN vê o botão de apagar nos outros', f'onclick="openDeleteUserModal({alvo.id})"' in html)
    t('mas não em si mesmo', f'openDeleteUserModal({chefe.id})' not in html)
    t('nem no superusuário do Django', f'openDeleteUserModal({root.id})' not in html)
    t('tem a janela de confirmação', 'id="deleteUserModal"' in html and 'name="confirmacao"' in html)
    blocos = [b for b in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, flags=re.S)
              if 'deleteUserModal' in b]
    t('um script cuida da janela', len(blocos) == 1, len(blocos))
    if blocos:
        valido, erro = sintaxe_ok(blocos[0])
        t('o JavaScript da janela tem sintaxe válida (node --check)', valido, erro)
    html = c_admin.get('/users/manage/users/').content.decode()
    t('a ADMINISTRAÇÃO não vê o botão nem a janela',
      'openDeleteUserModal(' not in html and 'id="deleteUserModal"' not in html)
    html = c_root.get('/users/manage/users/').content.decode()
    t('o superusuário vê o botão no SUPERADMIN', f'onclick="openDeleteUserModal({chefe.id})"' in html)

    print('\n== O QUE VAI JUNTO ==')
    r = c_chefe.get(url(alvo))
    j = r.json() if r.status_code < 500 else {}
    t('a prévia vem em JSON', r.status_code == 200 and j.get('ok') and j.get('username') == 'zzau.alvo',
      r.content[:200])
    t('lista as tarefas que vão junto',
      any(l['tipo'] == 'Tarefas' and l['quantidade'] >= 1 for l in j.get('apagados', [])), j.get('apagados'))
    t('sem bloqueio', j.get('bloqueios') == [], j.get('bloqueios'))
    r = c_chefe.get(url(com_cartao))
    t('cartão sob responsabilidade da pessoa bloqueia a exclusão',
      r.status_code == 200 and len(r.json().get('bloqueios', [])) == 1, r.content[:300])
    t('a ADMINISTRAÇÃO não consulta a prévia (403)', c_admin.get(url(alvo)).status_code == 403)
    t('usuário inexistente: 404', c_chefe.get('/users/manage/users/99999999/delete/').status_code == 404)

    print('\n== APAGAR ==')
    r = c_chefe.post(url(alvo), {'confirmacao': 'outro.nome'}, follow=True)
    t('confirmação errada não apaga', User.objects.filter(pk=alvo.pk).exists()
      and 'digite o usuário' in r.content.decode())
    c_admin.post(url(alvo), {'confirmacao': 'zzau.alvo'}, follow=True)
    t('a ADMINISTRAÇÃO não apaga', User.objects.filter(pk=alvo.pk).exists())
    c_chefe.post(url(chefe), {'confirmacao': 'zzau.chefe'}, follow=True)
    t('ninguém apaga a si mesmo', User.objects.filter(pk=chefe.pk).exists())
    c_chefe.post(url(root), {'confirmacao': 'zzau.root'}, follow=True)
    t('SUPERADMIN comum não apaga o superusuário', User.objects.filter(pk=root.pk).exists())
    r = c_chefe.post(url(com_cartao), {'confirmacao': 'zzau.cartao'}, follow=True)
    t('com registro protegido não apaga e explica a saída', User.objects.filter(pk=com_cartao.pk).exists()
      and 'Desative o usuário' in r.content.decode(), r.redirect_chain)
    t('GET não apaga', User.objects.filter(pk=alvo.pk).exists())

    r = c_chefe.post(url(alvo), {'confirmacao': 'zzau.alvo'}, follow=True)
    t('com a confirmação certa, apaga', not User.objects.filter(pk=alvo.pk).exists())
    t('e volta para a lista com o aviso', bool(r.redirect_chain)
      and r.redirect_chain[-1][0].endswith('/users/manage/users/') and 'apagado' in r.content.decode(),
      r.redirect_chain)
    t('as tarefas dele foram junto', not TaskActivity.objects.filter(pk=tarefa.pk).exists())
    t('fica registrado no log do sistema', SystemLog.objects.filter(
        user=chefe, action_type='ADMIN_ACTION', description__contains='zzau.alvo').exists())
    c_root.post(url(chefe), {'confirmacao': 'zzau.chefe'}, follow=True)
    t('o superusuário apaga um SUPERADMIN', not User.objects.filter(pk=chefe.pk).exists())

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
