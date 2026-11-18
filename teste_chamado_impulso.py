"""/projects/support/admin/template/ (18/11/2026): "Puxar para impulso e assumir" cria a meta e fechar o chamado conclui. Banco descartável."""
import io
import json
import os
import pathlib
import sys
import tempfile
import zipfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-chamado-impulso-'))
(PASTA / 'zz_chamado_impulso_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', 'NAME': r'{PASTA}/banco.sqlite3'}}}}


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
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-chimp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-chimp-2'}},
}}
""")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_chamado_impulso_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import requests
from django.contrib.auth import get_user_model
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import ChamadoNoImpulso, Meta
from projects.models_chat import SupportAgent, SupportChat, SupportChatMessage
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
    raise AssertionError('teste tentou sair para a rede')


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()

ti = Sector.objects.create(name='TI ZZ')
loja = Sector.objects.create(name='Loja ZZ')


def novo(u, **k):
    k.setdefault('hierarchy', 'PADRAO')
    return User.objects.create_user(username=f'zzc.{u}', email=f'zzc.{u}@exemplo-teste.local', password='S3nha!teste',
                                    first_name=u.title(), last_name='ZZ', **k)


chefe = novo('chefe', hierarchy='SUPERADMIN', is_superuser=True)
agente = novo('agente', sector=ti)
agente.sectors.add(ti)
gestora = novo('gestora', sector=ti)
sem_impulso = novo('semimpulso', sector=ti)
sem_impulso.sectors.add(ti)
outro = novo('outro', sector=ti)
outro.sectors.add(ti)
lojista = novo('lojista', sector=loja)
for u in (agente, sem_impulso, outro):
    SupportAgent.objects.create(user=u, is_active=True)
for nome_grupo, membros in (('ESCRITÓRIO (ADM)', [agente, outro]), ('GESTORES (IMPULSO)', [gestora])):
    g, _ = CommunicationGroup.objects.get_or_create(name=nome_grupo, defaults={'created_by': chefe})
    g.members.add(*membros)


def chamado(titulo='Impressora parou', **k):
    k.setdefault('status', 'ABERTO')
    k.setdefault('priority', 'ALTA')
    return SupportChat.objects.create(user=lojista, title=titulo, sector=ti, **k)


ca, cs, co, cl = Client(), Client(), Client(), Client()
ca.force_login(agente); cs.force_login(sem_impulso); co.force_login(outro); cl.force_login(lojista)

print('Botão no card')
c1 = chamado()
d = ca.get(f'/projects/support/chat/{c1.id}/').json()
t('agente com Impulso vê o botão', d['impulso']['pode_puxar'] is True and d['can_assume'], d.get('impulso'))
t('agente sem Impulso não vê', cs.get(f'/projects/support/chat/{c1.id}/').json()['impulso']['pode_puxar'] is False)
h = ca.get('/projects/support/admin/template/').content.decode()
t('a tela tem o botão e a função', 'Puxar para impulso e assumir' in h and 'function pullToImpulso' in h
  and '/projects/support/chat/${chatId}/impulso/' in h)

print('Puxar')
r = cs.post(f'/projects/support/chat/{c1.id}/impulso/')
t('sem Impulso: 403 e nada muda', r.status_code == 403 and not Meta.objects.exists()
  and SupportChat.objects.get(pk=c1.pk).assigned_to_id is None)
t('solicitante (não é agente) não puxa', cl.post(f'/projects/support/chat/{c1.id}/impulso/').status_code == 403)
r = ca.post(f'/projects/support/chat/{c1.id}/impulso/')
j = r.json()
c1.refresh_from_db()
m = Meta.objects.filter(colaborador=agente).first()
t('puxar assume o chamado (atendente e Em Andamento)', r.status_code == 200 and j['success'] and c1.assigned_to_id == agente.id
  and c1.status == 'EM_ANDAMENTO' and c1.started_at is not None, j)
t('cria a meta no Impulso da pessoa, aprovada e Em Andamento', m is not None and m.status == 'EM_ANDAMENTO'
  and m.aprovacao == 'APROVADA' and m.created_by_id == agente.id and m.prazo == timezone.localdate())
t('título com protocolo e prioridade do chamado', m and m.titulo == f'Chamado {c1.protocol} — Impressora parou'
  and m.prioridade == 'ALTA' and c1.protocol in m.descricao and f'?chat={c1.id}' in m.descricao)
t('gestor da meta é o gestor do Impulso do setor', m and m.gestor_id == gestora.id, m and m.gestor_id)
t('vai para o Impulso', j['redirect'] == '/impulso/metas/')
t('mensagem interna no chamado', SupportChatMessage.objects.filter(chat=c1, is_internal=True,
                                                                    message__contains='puxado para o Impulso').exists())
k = ca.get('/impulso/metas/')
t('a meta aparece no Kanban dela, em Em Andamento', k.status_code == 200 and any(
    col['status'] == 'EM_ANDAMENTO' and m.id in [x.id for x in col['metas']] for col in k.context['colunas']))
r = ca.post(f'/projects/support/chat/{c1.id}/impulso/')
t('puxar de novo não duplica', r.json()['success'] and r.json()['criada'] is False and Meta.objects.filter(colaborador=agente).count() == 1)
d = ca.get(f'/projects/support/chat/{c1.id}/').json()
t('depois de puxar: sem botão, com link para a meta', d['impulso']['pode_puxar'] is False and d['impulso']['meta_url'] == f'/impulso/metas/{m.id}/')
r = co.post(f'/projects/support/chat/{c1.id}/impulso/')
t('chamado de outro atendente não é puxado', r.status_code == 400 and not Meta.objects.filter(colaborador=outro).exists())
fora = SupportChat.objects.create(user=lojista, title='Outro setor', sector=loja, status='ABERTO')
t('chamado de outro setor não é puxado', ca.post(f'/projects/support/chat/{fora.id}/impulso/').status_code == 403)

print('Fechar o chamado conclui a meta')
r = ca.post(f'/projects/support/chat/{c1.id}/resolve/')
m.refresh_from_db()
t('Finalizar atendimento → meta Concluída', r.json()['success'] and m.status == 'CONCLUIDA' and m.avaliado_em is not None
  and c1.protocol in m.avaliacao_comentario, (m.status, m.avaliacao_comentario))
t('sem nota (não mexe na qualidade), com entrega registrada', m.nota_qualidade is None and m.entregue_em is not None)
t('chamado fechado não oferece o botão', ca.get(f'/projects/support/chat/{c1.id}/').json()['impulso']['pode_puxar'] is False)

c2 = chamado('Sem acesso ao e-mail', priority='URGENTE')
ca.post(f'/projects/support/chat/{c2.id}/impulso/')
m2 = Meta.objects.get(chamado_suporte__chamado=c2)
t('urgente vira prioridade Crítica', m2.prioridade == 'CRITICA')
r = ca.post(f'/projects/support/chat/{c2.id}/close/')
c2.refresh_from_db(); m2.refresh_from_db()
t('"Fechar ticket" agora grava o fechamento (antes não salvava)', r.json()['success'] and c2.status == 'FECHADO' and c2.closed_at)
t('e conclui a meta', m2.status == 'CONCLUIDA')

print('Reabrir')
c2.status = 'ABERTO'; c2.assigned_to = None; c2.save()
m2.refresh_from_db()
t('chamado reaberto: a meta volta para Em Andamento', m2.status == 'EM_ANDAMENTO' and m2.avaliado_em is None
  and 'reaberto' in m2.avaliacao_comentario)
c2.close_chat(); c2.save()
m2.refresh_from_db()
t('fechado de novo: conclui de novo', m2.status == 'CONCLUIDA')

print('Meta concluída pelo gestor não é reaberta')
c3 = chamado('Teclado')
ca.post(f'/projects/support/chat/{c3.id}/impulso/')
m3 = Meta.objects.get(chamado_suporte__chamado=c3)
Meta.objects.filter(pk=m3.pk).update(status='CONCLUIDA', nota_qualidade=5, nota_prazo=5)
c3.status = 'AGUARDANDO'; c3.save()
m3.refresh_from_db()
t('concluída pela avaliação continua concluída se o chamado mudar', m3.status == 'CONCLUIDA' and m3.nota_qualidade == 5)

print('Chamado sem vínculo')
c4 = chamado('Sem impulso')
c4.close_chat(); c4.save()
t('fechar chamado sem meta não cria nada', not ChamadoNoImpulso.objects.filter(chamado=c4).exists())
t('excluir o chamado apaga o vínculo mas não a meta', (lambda mid: (c1.delete(), Meta.objects.filter(pk=mid).exists())[1])(m.id)
  and not ChamadoNoImpulso.objects.filter(meta_id=m.id).exists())

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
