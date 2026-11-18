"""/communications/create/: anexar PDF e vídeo, além de imagem (18/11/2026).

Pedido: "Precisa dar para anexar PDF e a visualização ser correta como imagem ou video".
Cobre: a tela aceita imagem/vídeo/PDF; o servidor confere tipo, tamanho e se o PDF é PDF
antes de criar o comunicado (arquivo recusado não deixa comunicado pela metade); o detalhe
e o feed mostram a imagem como <img>, o vídeo com player e o PDF pelo visualizador
(PDF.js lendo o arquivo pelo próprio portal); o anexo é servido inline; a API do app
separa imagens (carrossel) de vídeo/PDF (abrem no navegador).

Banco e arquivos descartáveis (sqlite, syncdb); WhatsApp e rede bloqueados.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-com-anexos-'))
(PASTA / 'zz_com_anexos_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ca'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ca-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_com_anexos_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model
import requests

from communications import views as com_views
from communications.models import Communication, CommunicationImage

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
whatsapp = mock.patch('communications.whatsapp.enviar_whatsapp_comunicado').start()

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82')
PDF = b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n%%EOF\n'
MP4 = b'\x00\x00\x00\x18ftypmp42' + b'0' * 64


def arq(nome, conteudo, tipo):
    return SimpleUploadedFile(nome, conteudo, content_type=tipo)


chefe = User.objects.create_user(username='zzca.chefe', email='zzca.chefe@exemplo-teste.local',
                                 password='S3nha!teste', first_name='ZZ', last_name='Chefe',
                                 hierarchy='SUPERADMIN', is_superuser=True)
leitor = User.objects.create_user(username='zzca.leitor', email='zzca.leitor@exemplo-teste.local',
                                  password='S3nha!teste', first_name='ZZ', last_name='Leitor', hierarchy='PADRAO')
c = Client(); c.force_login(chefe)
cl = Client(); cl.force_login(leitor)


def criar(titulo, *arquivos, **extra):
    dados = {'title': titulo, 'message': '<p>Corpo do comunicado</p>', 'recipients': [str(leitor.id)],
             'photos': list(arquivos)}
    dados.update(extra)
    return c.post('/communications/create/', dados, follow=True)


print('== TELA DE CRIAR ==')
html = c.get('/communications/create/').content.decode()
t('o campo aceita imagem, vídeo e PDF', 'accept="image/*,video/*,application/pdf,.pdf"' in html)
t('e diz os limites', 'PDF até 30 MB' in html and 'até 100 MB' in html)

print('\n== CRIAR COM IMAGEM, VÍDEO E PDF ==')
r = criar('ZZ com anexos', arq('foto.png', PNG, 'image/png'), arq('treino.mp4', MP4, 'video/mp4'),
          arq('Regulamento da campanha.pdf', PDF, 'application/pdf'), **{'caption-1': 'Vídeo do treino'})
com = Communication.objects.filter(title='ZZ com anexos').first()
t('o comunicado é criado', com is not None, r.status_code)
anexos = list(com.images.order_by('order')) if com else []
t('com os três anexos na ordem', [a.tipo for a in anexos] == ['imagem', 'video', 'pdf'], [a.tipo for a in anexos])
t('o PDF sem legenda leva o nome do arquivo', anexos and anexos[-1].caption == 'Regulamento da campanha', anexos and anexos[-1].caption)
t('a legenda escolhida fica no vídeo', len(anexos) > 1 and anexos[1].caption == 'Vídeo do treino')

print('\n== ARQUIVO RECUSADO NÃO CRIA COMUNICADO ==')
for titulo, arquivo, trecho in (
        ('ZZ docx', arq('planilha.docx', b'PK\x03\x04zz', 'application/vnd.openxmlformats-officedocument'), 'não é imagem, vídeo nem PDF'),
        ('ZZ pdf falso', arq('falso.pdf', b'<html>nao sou pdf</html>', 'application/pdf'), 'não é um PDF válido'),
        ('ZZ vazio', arq('vazio.pdf', b'', 'application/pdf'), 'chegou vazio')):
    r = criar(titulo, arq('foto.png', PNG, 'image/png'), arquivo)
    t(f'{titulo}: recusa e explica', trecho in r.content.decode() and not Communication.objects.filter(title=titulo).exists())
with mock.patch.dict(com_views.LIMITE_DO_ANEXO, {'video': 32}):
    r = criar('ZZ video grande', arq('longo.mp4', MP4, 'video/mp4'))
t('vídeo acima do limite: recusa com o limite na mensagem',
  'o limite para vídeo é' in r.content.decode() and not Communication.objects.filter(title='ZZ video grande').exists())
t('nada recusado mandou WhatsApp', all('ZZ com anexos' in str(ch) for ch in whatsapp.call_args_list), whatsapp.call_args_list)

print('\n== DETALHE ==')
html = cl.get(f'/communications/{com.id}/').content.decode()
url_pdf = f'/communications/{com.id}/anexo/{anexos[2].id}/'
t('a imagem continua como <img>', f'src="{anexos[0].image.url}"' in html)
t('o vídeo tem player', '<video src="' + anexos[1].image.url + '" controls playsinline' in html)
t('o PDF vai para o visualizador pelo portal', f'data-com-pdf="{url_pdf}"' in html and 'Regulamento da campanha' in html)
t('o PDF não vira <img> quebrada', f'<img src="{anexos[2].image.url}"' not in html)
t('o detalhe mostra todas as páginas', 'data-so-primeira' not in html)
t('o script dos anexos entra uma vez', html.count('var PDFJS =') == 1, html.count('var PDFJS ='))

print('\n== FEED DA HOME ==')
r = cl.get('/')
html = r.content.decode()
t('a home abre', r.status_code == 200, r.status_code)
t('no feed o PDF mostra a 1ª página e o botão das outras', f'data-com-pdf="{url_pdf}"' in html and 'data-so-primeira="1"' in html)
t('e o vídeo tem player', '<video src="' + anexos[1].image.url + '"' in html)

print('\n== O ANEXO SERVIDO PELO PORTAL ==')
r = cl.get(url_pdf)
corpo = b''.join(r.streaming_content) if r.status_code == 200 else b''
t('PDF inline, com o conteúdo', r.status_code == 200 and r['Content-Type'] == 'application/pdf'
  and r['Content-Disposition'].startswith('inline') and corpo == PDF, (r.status_code, r.get('Content-Type')))
t('pode abrir dentro da própria página', r.get('X-Frame-Options') == 'SAMEORIGIN', r.get('X-Frame-Options'))
outro = Communication.objects.create(title='ZZ outro', message='x', sender=chefe)
t('anexo de outro comunicado: 404', cl.get(f'/communications/{outro.id}/anexo/{anexos[2].id}/').status_code == 404)
t('sem login não abre', Client().get(url_pdf).status_code in (302, 401, 403))

print('\n== API DO APP ==')
from django.test import RequestFactory
from api_mobile import dados
req = RequestFactory().get('/api/mobile/v1/comunicados/')
req.user = leitor
res = dados.comunicado_resumo(req, Communication.objects.prefetch_related('images').get(pk=com.id))
t('o carrossel do app só leva a imagem', len(res['imagens']) == 1 and res['imagens'][0].endswith('.png'), res['imagens'])
t('vídeo e PDF vão em anexos', [(a['tipo'], a['nome']) for a in res['anexos']]
  == [('video', 'Vídeo do treino'), ('pdf', 'Regulamento da campanha')], res['anexos'])

print(f'\n{ok} OK / {fail} falhas')
print(f'(banco e arquivos descartáveis em {PASTA} — pode apagar)')
sys.exit(1 if fail else 0)
