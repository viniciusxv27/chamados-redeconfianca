"""/impulso/metas/ (28/10/2026): Kanban rápido, "Criadas por mim", aviso ao SUPERADMIN e mídia na descrição.

Pedidos:
- para quem vê todas as metas a tela levava minutos: cada card fazia ~9 consultas
  (novidades, aviso de exclusão, to-do) e a coluna Concluída trazia o mês inteiro;
- filtro "Metas criadas por mim";
- o SUPERADMIN recebe notificação do status das metas que ele criou;
- foto ou áudio opcionais na descrição.

Cobre:
- as novidades e o "o que some junto" calculados em lote batem com os métodos do modelo;
- o número de consultas não cresce com a quantidade de cards;
- a Concluída abre com 40 e o "Mostrar mais" traz o resto, com os mesmos filtros;
- "Criadas por mim" recorta e fica guardado para o Voltar;
- arrastar, entregar e avaliar avisam o SUPERADMIN criador (não quem mexeu, sem repetir o aviso do gestor);
- foto e áudio na criação, na edição (trocar e tirar), na ficha, na repetição e recusa de formato.

Roda num sqlite descartável; os arquivos vão para uma pasta temporária.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: a tabela das entregas e a coluna
# `exige_entrega` só existem no Postgres compartilhado depois do migrate, e o
# migrate é dele. O schema aqui sai direto dos models (`--run-syncdb`), como em
# qualquer outra sessão — nada toca o banco de verdade nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-kanban-rapido-'))
(PASTA / 'zz_kanban_rapido_settings.py').write_text(f"""
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
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-kr'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-kr-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_kanban_rapido_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from communications.models import CommunicationGroup
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


import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Notification
from impulso.models import Meta, MetaAnexo, MetaComentario, MetaItem, MetaVisualizacao
from impulso.views import _preparar_cards

loja = Sector.objects.create(name='ZZ Escritório Kanban')
chefe = User.objects.create_user(username='zzk.super', email='zzk.super@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZK', last_name='Super', sector=loja, is_superuser=True, hierarchy='SUPERADMIN')
gestor = User.objects.create_user(username='zzk.gestor', email='zzk.gestor@exemplo-teste.local', password='S3nha!teste',
                                  first_name='ZZK', last_name='Gestor', sector=loja)
ana = User.objects.create_user(username='zzk.ana', email='zzk.ana@exemplo-teste.local', password='S3nha!teste',
                               first_name='ZZK', last_name='Ana', sector=loja)
adm = CommunicationGroup.objects.create(name="ADM's LOJAS", created_by=chefe)
gestores = CommunicationGroup.objects.create(name='GESTORES (IMPULSO)', created_by=chefe)
for p in (chefe, gestor, ana):
    p.communication_groups.add(adm)
gestor.communication_groups.add(gestores)

hoje = timezone.localdate()


def meta(titulo, status=Meta.Status.A_FAZER, criador=chefe, prazo=None, **kw):
    return Meta.objects.create(gestor=gestor, colaborador=ana, titulo=titulo, descricao='ZZK', prazo=prazo or hoje,
                               status=status, aprovacao=Meta.Aprovacao.APROVADA, created_by=criador, **kw)


print('Novidades e aviso de exclusão em lote')
m1 = meta('ZZK com novidades')
MetaComentario.objects.create(meta=m1, autor=ana, mensagem='oi')
MetaComentario.objects.create(meta=m1, autor=chefe, mensagem='meu')
MetaItem.objects.create(meta=m1, texto='passo', concluido=True, concluido_por=ana, concluido_em=timezone.now())
MetaItem.objects.create(meta=m1, texto='passo 2')
MetaAnexo.objects.create(meta=m1, tipo='LINK', url='https://exemplo-teste.local/x', enviado_por=ana)
m2 = meta('ZZK vista depois')
MetaComentario.objects.create(meta=m2, autor=ana, mensagem='antes')
MetaVisualizacao.objects.create(meta=m2, user=chefe, visto_em=timezone.now() + datetime.timedelta(minutes=1))
lote = list(Meta.objects.filter(id__in=[m1.id, m2.id]).prefetch_related('itens'))
_preparar_cards(lote, chefe, {v.meta_id: v.visto_em for v in MetaVisualizacao.objects.filter(user=chefe)})
for m in lote:
    fresca = Meta.objects.get(pk=m.pk)
    t(f'novidades iguais ao método ({m.titulo})', m.novidades == fresca.novidades_para(chefe), (m.novidades, fresca.novidades_para(chefe)))
    t(f'impacto igual ao método ({m.titulo})', m.impacto_da_exclusao == fresca.impacto_da_exclusao)

print('Kanban de quem vê tudo')
cs, cg, ca = Client(), Client(), Client()
cs.force_login(chefe); cg.force_login(gestor); ca.force_login(ana)
for n in range(60):
    meta(f'ZZK concluída {n:02d}', status=Meta.Status.CONCLUIDA, prazo=hoje.replace(day=1), nota_qualidade=4, nota_prazo=4)
with CaptureQueriesContext(connection) as poucas:
    r = cs.get('/impulso/metas/')
for n in range(60, 90):
    meta(f'ZZK concluída {n:02d}', status=Meta.Status.CONCLUIDA, prazo=hoje.replace(day=1), nota_qualidade=4, nota_prazo=4)
for n in range(15):
    meta(f'ZZK a fazer {n:02d}')
with CaptureQueriesContext(connection) as muitas:
    r = cs.get('/impulso/metas/')
h = r.content.decode()
t('abre (200)', r.status_code == 200, r.status_code)
t('as consultas não crescem com os cards', len(muitas.captured_queries) <= len(poucas.captured_queries) + 2,
  (len(poucas.captured_queries), len(muitas.captured_queries)))
col = next(c for c in r.context['colunas'] if c['status'] == 'CONCLUIDA')
t('a Concluída abre com 40 e conta todas', len(col['metas']) == 40 and col['total'] == 90 and col['restantes'] == 50,
  (len(col['metas']), col['total'], col['restantes']))
t('o botão "Mostrar mais" aparece', 'imp-mais' in h and 'data-restantes>50<' in h)
r = cs.get('/impulso/metas/concluidas/', {'inicio': 40})
j = r.json()
t('o "Mostrar mais" traz as próximas 40', j['html'].count('data-meta-id=') == 40 and j['restantes'] == 10 and j['proximo'] == 80, j['restantes'])
j = cs.get('/impulso/metas/concluidas/', {'inicio': 80}).json()
t('e as últimas 10', j['html'].count('data-meta-id=') == 10 and j['restantes'] == 0)
t('os cards novos vêm com o botão de excluir', 'imp-excluir' in j['html'])

print('Criadas por mim')
dele = meta('ZZK criada pelo gestor', criador=gestor)
r = cg.get('/impulso/metas/', {'criadas_por_mim': '1'})
ids = {m.id for c in r.context['colunas'] for m in c['metas']}
t('só as que ele criou', ids == {dele.id}, ids)
t('o botão fica marcado', 'aria-pressed="true"' in r.content.decode())
t('fica guardado para o Voltar', cg.session['impulso_kanban_filtros'].get('criadas_por_mim') == '1')
t('a Concluída do "Mostrar mais" também recorta', cg.get('/impulso/metas/concluidas/', {'criadas_por_mim': '1'}).json()['html'] == ''
  or 'ZZK concluída' not in cg.get('/impulso/metas/concluidas/', {'criadas_por_mim': '1'}).json()['html'])

print('Aviso ao SUPERADMIN que criou')
alvo = meta('ZZK acompanhada')
Notification.objects.all().delete()
ca.post(f'/impulso/metas/{alvo.id}/status/', {'status': 'EM_ANDAMENTO'})
n = Notification.objects.filter(user=chefe).first()
t('arrastar avisa o criador', n and 'ZZK acompanhada' in n.message and 'A Fazer' in n.message and 'Em Andamento' in n.message.title()
  , n and n.message)
cs.post(f'/impulso/metas/{alvo.id}/status/', {'status': 'A_FAZER'})
t('quem mexeu não recebe aviso de si mesmo', Notification.objects.filter(user=chefe).count() == 1)
ca.post(f'/impulso/metas/{alvo.id}/entregar/')
t('entregar avisa o criador', Notification.objects.filter(user=chefe, title__icontains='entregue').exists())
cg.post(f'/impulso/metas/{alvo.id}/avaliar/', {'nota_qualidade': 5, 'nota_prazo': 4})
t('avaliar avisa o criador', Notification.objects.filter(user=chefe, title__icontains='conclu').exists())
Notification.objects.all().delete()
outra = meta('ZZK criada pelo gestor 2', criador=gestor)
ca.post(f'/impulso/metas/{outra.id}/status/', {'status': 'EM_ANDAMENTO'})
t('meta criada por quem não é SUPERADMIN não avisa ninguém a mais', not Notification.objects.filter(user=gestor).exists())
Notification.objects.all().delete()
dupla = Meta.objects.create(gestor=chefe, colaborador=ana, titulo='ZZK chefe é gestor', descricao='x', prazo=hoje,
                            aprovacao=Meta.Aprovacao.APROVADA, created_by=chefe)
ca.post(f'/impulso/metas/{dupla.id}/status/', {'status': 'ENTREGUE'})
t('sem aviso repetido quando o criador já é o gestor avisado', Notification.objects.filter(user=chefe).count() == 1,
  list(Notification.objects.filter(user=chefe).values_list('title', flat=True)))

print('Foto e áudio na descrição')
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82')
r = cs.post('/impulso/metas/nova/', {'colaborador': ana.id, 'titulo': 'ZZK com mídia', 'descricao': 'veja a foto',
                                      'prazo': hoje.isoformat(), 'recorrencia': 'DIARIA',
                                      'descricao_foto': SimpleUploadedFile('loja.png', PNG, content_type='image/png'),
                                      'descricao_audio': SimpleUploadedFile('explica.webm', b'zz' * 50, content_type='audio/webm')})
comm = Meta.objects.filter(titulo='ZZK com mídia').first()
t('a criação guarda foto e áudio', comm and comm.descricao_foto.name and comm.descricao_audio.name.endswith('.webm'), r.status_code)
h = cs.get(f'/impulso/metas/{comm.id}/').content.decode() if comm else ''
t('a ficha mostra a foto e o player', 'Foto da descrição' in h and '<audio controls' in h)
t('o formulário aceita arquivo', 'enctype="multipart/form-data"' in cs.get('/impulso/metas/nova/').content.decode())
r = cs.post('/impulso/metas/nova/', {'colaborador': ana.id, 'titulo': 'ZZK mídia errada', 'descricao': 'x', 'prazo': hoje.isoformat(),
                                      'descricao_foto': SimpleUploadedFile('virus.exe', b'MZ', content_type='application/octet-stream')},
            follow=True)
t('formato errado volta com aviso, sem criar', 'precisa ser uma imagem' in r.content.decode()
  and not Meta.objects.filter(titulo='ZZK mídia errada').exists())
if comm:
    comm.nota_qualidade = comm.nota_prazo = 5
    comm.status = Meta.Status.CONCLUIDA
    comm.save()
    proxima = comm.criar_proxima_ocorrencia()
    t('a repetição leva a mesma foto e o mesmo áudio', proxima and proxima.descricao_foto.name == comm.descricao_foto.name
      and proxima.descricao_audio.name == comm.descricao_audio.name)
    alvo2 = proxima
    r = cs.post(f'/impulso/metas/{alvo2.id}/editar/', {'titulo': alvo2.titulo, 'descricao': 'nova', 'prazo': alvo2.prazo.isoformat(),
                                                        'recorrencia': alvo2.recorrencia, 'tirar_audio': '1',
                                                        'descricao_foto': SimpleUploadedFile('outra.jpg', PNG, content_type='image/jpeg')})
    alvo2.refresh_from_db()
    t('editar troca a foto e tira o áudio', alvo2.descricao_foto.name != comm.descricao_foto.name and not alvo2.descricao_audio)
    t('a original continua com os dela', Meta.objects.get(pk=comm.pk).descricao_audio.name == comm.descricao_audio.name)
    t('a edição vira comentário', MetaComentario.objects.filter(meta=alvo2, mensagem__icontains='foto/áudio').exists())

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
