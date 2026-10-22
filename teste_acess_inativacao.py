"""Acess (22/10/2026): usuário inativado no portal → aviso no Acess (desligamento imediato ou agendado, PPL).

- inativar no portal (qualquer save) cria o aviso e notifica quem gerencia desligamentos;
- faixa em todas as telas do Acess e seção no topo de Desligamentos;
- "Desligar agora" → desligamento hoje ("Ação manual pendente") + notificação;
- "Agendar" → vai para Agendados; no dia, a tarefa diária notifica;
- "Sem desligamento" exige motivo; reativar no portal encerra o aviso.

Transação desfeita no fim; nada sai para a rede.
"""
import os
import sys
from datetime import timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ai'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ai2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from acess import servicos as sv
from acess.models import AcessoAcess, AvisoInativacao, Colaborador
from acess.permissoes import CHAVES
from core import agendador_diario
from core.models import Notification
from users.models import Sector

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


def sem_rede(*a, **k):
    raise AssertionError('o teste tentou sair para a rede')


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede):
        hoje = timezone.localdate()
        loja = Sector.objects.create(name='Loja ZZ Inativa', adabas='ZZI0001-001')
        op = User.objects.create_user(username='zz.ai.op', email='zz.ai.op@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='Operadora', hierarchy='ADMIN')
        AcessoAcess.objects.create(user=op, permissoes=['desligamentos.gerenciar'])
        co = Client()
        co.force_login(op)

        def pessoa(n, cpf):
            u = User.objects.create_user(username=f'zz.ai.{n}', email=f'zz.ai.{n}@exemplo-teste.local', password='x',
                                         first_name='ZZ', last_name=f'Pessoa {n}', sector=loja)
            c = sv.criar_colaborador({'nome': f'ZZ Pessoa {n}', 'cpf': cpf}, loja, usuario=op, user=u)
            return u, c

        u1, c1 = pessoa('um', '52998224725')
        u2, c2 = pessoa('dois', '11144477735')
        u3, c3 = pessoa('tres', '39053344705')
        u4, c4 = pessoa('quatro', '86288366757')

        print('— Inativar no portal')
        antes = Notification.objects.filter(user=op).count()
        u1.is_active = False
        u1.save()
        a1 = AvisoInativacao.objects.filter(colaborador=c1).first()
        t('inativar cria o aviso no Acess', a1 is not None and a1.status == AvisoInativacao.PENDENTE)
        t('e notifica quem gerencia desligamentos', Notification.objects.filter(user=op).count() == antes + 1
          and Notification.objects.filter(user=op, title__contains='inativado no portal').exists())
        u1.save()
        t('salvar de novo não duplica', AvisoInativacao.objects.filter(colaborador=c1).count() == 1)
        u1.first_name = 'ZZ'
        u1.save(update_fields=['first_name'])
        t('save que não mexe em is_active não faz nada', AvisoInativacao.objects.filter(colaborador=c1).count() == 1)
        for u in (u2, u3, u4):
            u.is_active = False
            u.save()
        t('um aviso por pessoa', AvisoInativacao.objects.filter(status='PENDENTE', colaborador__in=[c1, c2, c3, c4]).count() == 4)

        print('— Telas')
        h = co.get('/acess/lojas/').content.decode()
        t('faixa em todas as telas do Acess', 'inativada(s) no portal aguardam decisão' in h)
        r = co.get('/acess/desligamentos/')
        h = r.content.decode()
        t('seção no topo de Desligamentos', 'id="inativados"' in h and 'ZZ PESSOA UM' in h and 'Desligar agora' in h
          and 'name="execucao"' in h)
        t('contador da aba soma os avisos', r.context['contadores']['desligamentos'] >= 4)

        print('— Decidir')
        antes = Notification.objects.filter(user=op).count()
        co.post(f'/acess/desligamentos/inativado/{a1.id}/', {'decisao': 'imediato'})
        a1.refresh_from_db()
        c1.refresh_from_db()
        t('desligar agora: execução hoje', a1.status == 'IMEDIATO' and c1.desligamento_em == hoje
          and 'inativado no portal' in c1.desligamento_motivo)
        t('e já notifica o desligamento de hoje', Notification.objects.filter(user=op, title__contains='hoje').count() >= 1
          and Notification.objects.filter(user=op).count() == antes + 1)
        h = co.get('/acess/desligamentos/').content.decode()
        i = h.index('Ação manual pendente (')
        t('aparece em Ação manual pendente', 'ZZ PESSOA UM' in h[i:h.index('Agendados (')])
        daqui = hoje + timedelta(days=10)
        a2 = AvisoInativacao.objects.get(colaborador=c2)
        co.post(f'/acess/desligamentos/inativado/{a2.id}/', {'decisao': 'agendar', 'execucao': daqui.isoformat()})
        a2.refresh_from_db()
        c2.refresh_from_db()
        t('agendar: vai para Agendados com o dia escolhido', a2.status == 'AGENDADO' and c2.desligamento_em == daqui
          and c2.status == Colaborador.ATIVO)
        a3 = AvisoInativacao.objects.get(colaborador=c3)
        co.post(f'/acess/desligamentos/inativado/{a3.id}/', {'decisao': 'agendar', 'execucao': ''})
        a3.refresh_from_db()
        t('agendar sem dia é recusado', a3.status == 'PENDENTE')
        co.post(f'/acess/desligamentos/inativado/{a3.id}/', {'decisao': 'descartar', 'observacao': ''})
        a3.refresh_from_db()
        t('sem desligamento exige motivo', a3.status == 'PENDENTE')
        co.post(f'/acess/desligamentos/inativado/{a3.id}/', {'decisao': 'descartar', 'observacao': 'inativado por engano'})
        a3.refresh_from_db()
        t('sem desligamento com motivo encerra o aviso', a3.status == 'DESCARTADO' and a3.decidido_por == op)
        u4.is_active = True
        u4.save()
        t('reativar no portal encerra o aviso pendente', AvisoInativacao.objects.get(colaborador=c4).status == 'DESCARTADO')

        print('— No dia escolhido')
        antes = Notification.objects.filter(user=op).count()
        r = sv.avisar_desligamentos_do_dia(daqui)
        t('a tarefa do dia notifica', '1 desligamento' in r and Notification.objects.filter(user=op).count() == antes + 1
          and Notification.objects.filter(user=op, message__contains='ZZ PESSOA DOIS').exists(), r)
        t('registrada no agendador diário', any(x[0] == 'acess_desligamentos_do_dia' for x in agendador_diario.TAREFAS))
        sem = User.objects.create_user(username='zz.ai.sem', email='zz.ai.sem@exemplo-teste.local', password='x')
        sem.is_active = False
        sem.save()
        t('quem não tem ficha no Acess não gera aviso', not AvisoInativacao.objects.filter(user=sem).exists())
        r = Client()
        r.force_login(User.objects.create_user(username='zz.ai.x', email='zz.ai.x@exemplo-teste.local', password='x'))
        t('quem não gerencia desligamentos não decide',
          r.post(f'/acess/desligamentos/inativado/{a2.id}/', {'decisao': 'descartar', 'observacao': 'x'}).status_code == 302
          and AvisoInativacao.objects.get(pk=a2.pk).status == 'AGENDADO')
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
