"""Excluir tarefas em /users/tasks/.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import timedelta

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
from django.urls import reverse
from django.utils import timezone

from core.models import TaskActivity, TaskMessage
from users.models import Sector
from users.views import pode_excluir_tarefa

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
    area = Sector.objects.create(name='ZZ Area Tarefa')

    def novo(username, **kw):
        return User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', sector=area,
            first_name=username.split('.')[1].title(), last_name='Teste', **kw)

    dono = novo('tk.dono', hierarchy='PADRAO')
    colega = novo('tk.colega', hierarchy='PADRAO')
    chefe = novo('tk.chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)

    prazo = timezone.now() + timedelta(days=3)

    def tarefa(titulo, para, por):
        return TaskActivity.objects.create(
            title=titulo, description='x', assigned_to=para, created_by=por,
            due_date=prazo, status='PENDING')

    minha = tarefa('ZZ minha tarefa', dono, dono)          # criada por ela mesma
    do_chefe = tarefa('ZZ do chefe', dono, chefe)          # atribuída pelo gestor
    de_outro = tarefa('ZZ de outro', colega, colega)       # nem dela nem para ela

    print('== A REGRA ==')
    t('quem criou a própria tarefa pode apagar', pode_excluir_tarefa(dono, minha))
    t('mas não a que o gestor atribuiu a ela',
      not pode_excluir_tarefa(dono, do_chefe))
    t('o gestor apaga qualquer uma', pode_excluir_tarefa(chefe, do_chefe)
      and pode_excluir_tarefa(chefe, minha))
    t('ninguém apaga a tarefa de terceiro', not pode_excluir_tarefa(dono, de_outro))
    t('anônimo não apaga nada', not pode_excluir_tarefa(None, minha))

    print('\n== A TELA ==')
    c = Client(); c.force_login(dono)
    html = c.get('/users/tasks/').content.decode()
    t('a tarefa própria traz o botão de excluir',
      f"excluirTarefa({minha.id}," in html)
    t('a atribuída pelo gestor não traz',
      f"excluirTarefa({do_chefe.id}," not in html)
    t('o card é identificável para sumir da tela',
      f'data-task-id="{minha.id}"' in html)
    t('o aviso diz o que vai junto',
      'as mensagens, os anexos e as subtarefas' in html)
    t('e que não dá para desfazer', 'Não dá para desfazer' in html)

    cch = Client(); cch.force_login(chefe)
    html = cch.get('/users/tasks/').content.decode()
    t('o gestor não vê tarefa que não é dele na própria tela',
      f"excluirTarefa({minha.id}," not in html)

    print('\n== EXCLUIR DE VERDADE ==')
    TaskMessage.objects.create(task=minha, user=dono, message='ZZ recado')
    t('a tarefa tem mensagem antes', TaskMessage.objects.filter(task=minha).exists())

    url = reverse('delete_task', args=[minha.id])
    r = c.post(url)
    t('a resposta é json de sucesso', r.json().get('success') is True, r.content[:120])
    t('a tarefa some', not TaskActivity.objects.filter(id=minha.id).exists())
    t('a mensagem vai junto (cascade)',
      not TaskMessage.objects.filter(task_id=minha.id).exists())
    t('a mensagem diz o título', 'ZZ minha tarefa' in r.json().get('message', ''))

    print('\n== O SERVIDOR NÃO CONFIA NA TELA ==')
    r = c.post(reverse('delete_task', args=[do_chefe.id]))
    t('POST forjado na tarefa do gestor é recusado', r.status_code == 403, r.status_code)
    t('e ela continua lá', TaskActivity.objects.filter(id=do_chefe.id).exists())
    t('a resposta explica', 'Permissão negada' in r.json().get('error', ''))

    r = c.post(reverse('delete_task', args=[de_outro.id]))
    t('tarefa de terceiro também é recusada', r.status_code == 403)
    t('e continua lá', TaskActivity.objects.filter(id=de_outro.id).exists())

    r = c.get(reverse('delete_task', args=[do_chefe.id]))
    t('GET não apaga (405)', r.status_code == 405, r.status_code)

    print('\n== O GESTOR APAGA ==')
    r = cch.post(reverse('delete_task', args=[do_chefe.id]))
    t('o gestor apaga a que ele atribuiu', r.json().get('success') is True)
    t('e ela some', not TaskActivity.objects.filter(id=do_chefe.id).exists())

    print('\n== SEM LOGIN ==')
    anon = Client()
    r = anon.post(reverse('delete_task', args=[de_outro.id]))
    t('sem login é mandado para o login', r.status_code in (302, 403), r.status_code)
    t('e a tarefa continua lá', TaskActivity.objects.filter(id=de_outro.id).exists())

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
