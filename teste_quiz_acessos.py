"""Quiz: o SUPERADMIN libera quem pode criar quiz, numa lista só.

Pedido: "Permita SUPERADMIN liberar usuários individualmente a criar quiz".

A liberação já existia — a chave `quiz.gestao` na edição de cada usuário —, mas
para liberar três pessoas era preciso abrir três cadastros e achar a caixinha.
Agora há `/quiz/acessos/`, com a lista à vista; por baixo é a **mesma** chave,
não uma segunda lista parecida.

O que este teste cobre:

- quem é liberado passa a criar quiz de verdade (`pode_gerenciar`);
- a tela de liberar é só do SUPERADMIN;
- tirar a liberação tira o acesso, e o que a pessoa criou continua;
- a lista é a mesma do catálogo de acessos (`quiz.gestao`);
- o aviso cai no sino de quem foi liberado.

Roda dentro de uma transação desfeita.
"""
import os
import re
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-qz-ac'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-qz-ac2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from core.models import Notification
from quiz.permissoes import pode_gerenciar
from users.models import Sector, UserModuleAccess

User = get_user_model()
ok = fail = 0
ACESSOS = '/quiz/acessos/'


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
    setor = Sector.objects.create(name='ZZ Setor Quiz Acessos')
    chefe = User.objects.create_user(
        username='zzqa.chefe', email='zzqa.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', sector=setor)
    ana = User.objects.create_user(
        username='zzqa.ana', email='zzqa.ana@exemplo-teste.local', password='S3nha!teste',
        first_name='Ana', last_name='Quiz', hierarchy='PADRAO', sector=setor)
    bruno = User.objects.create_user(
        username='zzqa.bruno', email='zzqa.bruno@exemplo-teste.local', password='S3nha!teste',
        first_name='Bruno', last_name='Quiz', hierarchy='PADRAO', sector=setor)

    c = Client(); c.force_login(chefe)
    ca = Client(); ca.force_login(ana)

    print('== ANTES DE LIBERAR ==')
    t('a Ana não cria quiz', not pode_gerenciar(ana))
    t('e não entra na tela de novo quiz', ca.get('/quiz/quizzes/novo/').status_code in (302, 403))
    t('nem na lista de acessos', ca.get(ACESSOS).status_code == 302)

    print('\n== LIBERANDO ==')
    r = c.post(ACESSOS, {'user': ana.id})
    # As liberações ficam em cache na instância do usuário (o menu consulta
    # várias por requisição); recarregar do banco é o que o próximo request faz.
    ana = User.objects.get(pk=ana.pk)
    t('o SUPERADMIN libera pela lista', r.status_code == 302
      and UserModuleAccess.objects.filter(user=ana, module_key='quiz.gestao').exists())
    t('e a Ana passa a criar quiz', pode_gerenciar(ana))
    t('a tela de novo quiz abre para ela', ca.get('/quiz/quizzes/novo/').status_code == 200)
    t('o aviso cai no sino dela',
      Notification.objects.filter(user=ana, title__icontains='criar quiz').exists())
    t('é a mesma chave da edição de usuário, não uma lista nova',
      UserModuleAccess.objects.filter(user=ana).values_list('module_key', flat=True)[0]
      == 'quiz.gestao')

    html = c.get(ACESSOS).content.decode()
    t('a lista mostra quem está liberado', 'Ana Quiz' in html)
    t('e quem já pode por ser SUPERADMIN, à parte', 'ZZ Chefe' in html
      and 'por serem SUPERADMIN' in html)
    t('quem já está liberado sai do seletor de liberar',
      not re.search(r'<option value="%s"' % ana.id, html), '')

    r = c.post(ACESSOS, {'user': ana.id})
    t('liberar de novo não duplica',
      UserModuleAccess.objects.filter(user=ana, module_key='quiz.gestao').count() == 1)
    c.post(ACESSOS, {'user': chefe.id})
    t('e não adianta liberar quem já é SUPERADMIN',
      not UserModuleAccess.objects.filter(user=chefe).exists())
    c.post(ACESSOS, {'user': '0'})
    t('escolha vazia não quebra a tela', c.get(ACESSOS).status_code == 200)

    print('\n== TIRANDO ==')
    r = c.post(f'/quiz/acessos/{ana.id}/remover/')
    ana = User.objects.get(pk=ana.pk)
    t('a liberação sai', not UserModuleAccess.objects.filter(user=ana).exists())
    t('e a Ana volta a não criar quiz', not pode_gerenciar(ana))

    print('\n== SÓ O SUPERADMIN MEXE ==')
    c.post(ACESSOS, {'user': bruno.id})
    bruno = User.objects.get(pk=bruno.pk)
    cb = Client(); cb.force_login(bruno)
    t('quem foi liberado cria quiz, mas não libera ninguém',
      pode_gerenciar(bruno) and cb.get(ACESSOS).status_code == 302)
    cb.post(f'/quiz/acessos/{bruno.id}/remover/')
    t('nem tira a própria liberação por fora',
      UserModuleAccess.objects.filter(user=bruno, module_key='quiz.gestao').exists())

    t('o botão da lista aparece no quiz para o SUPERADMIN',
      ACESSOS in c.get('/quiz/').content.decode())
    t('e não aparece para quem só cria', ACESSOS not in cb.get('/quiz/').content.decode())
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
