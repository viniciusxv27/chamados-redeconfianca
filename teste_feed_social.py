"""Home de 25/11/2026: cartão de reconhecimento na ordem nova, cartão do Impulso e o feed da rede (/social/).

Cobre:
- reconhecimento: foto e nome de quem fez → comentário → foto e nome de quem foi reconhecido;
- cartão do Impulso: só para quem é do Impulso (204 para os outros), pontuação, faixa, dicas com link;
- feed: publicar foto (lida pelo Pillow) e vídeo (cabeçalho MP4), recusar o que não é mídia, curtir/descurtir,
  comentar (aviso ao dono), só os 3 últimos comentários no feed, "ver todos", apagar (dono, SUPERADMIN, não um
  colega), página seguinte pelo id, a seção na home e o link no menu.

Roda num sqlite descartável; nada sai para a rede.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco descartável.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-social-'))
(PASTA / 'zz_social_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-social'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-social-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_social_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import io
import json
from datetime import timedelta

import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from compliments.models import Compliment
from core.models import Notification
from social.models import Comentario, Post
from users.models import Sector, UserModuleAccess

User = get_user_model()
OK = FALHAS = 0


def t(nome, cond, extra=''):
    global OK, FALHAS
    if cond:
        OK += 1
        print('  OK  ', nome)
    else:
        FALHAS += 1
        print('  FALHA', nome, extra)


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()

loja = Sector.objects.create(name='Loja Glória')


def pessoa(nome, **extra):
    u = User.objects.create_user(username=f'zzso.{nome}', email=f'zzso.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name=nome.title(), last_name='Teste', sector=loja, **extra)
    c = Client(); c.force_login(u)
    return u, c


ana, ca = pessoa('ana')
bia, cb = pessoa('bia')
chefe, cs = pessoa('chefe', hierarchy='SUPERADMIN', is_superuser=True)


def foto(nome='foto.jpg', tam=(800, 600)):
    buf = io.BytesIO()
    Image.new('RGB', tam, (255, 107, 53)).save(buf, 'JPEG')
    return SimpleUploadedFile(nome, buf.getvalue(), content_type='image/jpeg')


def video(nome='video.mp4'):
    return SimpleUploadedFile(nome, b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 200, content_type='video/mp4')


print('Reconhecimento: quem fez → comentário → quem foi reconhecido')
Compliment.objects.create(from_user=bia, to_user=ana, rating=5, comment='ZZCOMENTARIO salvou o fechamento')
h = ca.get('/').content.decode()
i_de, i_txt, i_para = h.find('data-rc-reco-de'), h.find('ZZCOMENTARIO'), h.find('data-rc-reco-para')
t('a ordem do cartão é quem fez, o comentário e quem foi reconhecido', -1 < i_de < i_txt < i_para, (i_de, i_txt, i_para))
bloco_de = h[i_de:i_txt]
bloco_para = h[i_para:i_para + 2500]
t('o topo traz o nome de quem reconheceu', 'Bia Teste' in bloco_de and 'reconheceu' in bloco_de)
t('o fim traz o nome de quem foi reconhecido', 'Ana Teste' in bloco_para and 'Reconhecido' in bloco_para)

print('Cartão do Impulso')
t('quem não é do Impulso não tem o espaço do cartão na home', 'data-impulso-home' not in h)
t('e o endereço responde vazio (204)', ca.get('/impulso/resumo-home/').status_code == 204)
UserModuleAccess.objects.create(user=ana, module_key='impulso')
h = ca.get('/').content.decode()
t('quem é do Impulso tem o espaço que carrega depois', 'data-impulso-home' in h and '/impulso/resumo-home/' in h)
r = ca.get('/impulso/resumo-home/')
hc = r.content.decode()
t('o cartão traz pontuação, faixa e o link do Impulso', r.status_code == 200 and 'Seu Impulso' in hc
  and 'ihc-faixa' in hc and 'Abrir o Impulso' in hc, r.status_code)
from impulso.resumo_home import resumo  # noqa: E402
dados = resumo(ana)
t('dicas: no máximo 3, todas com texto', len(dados['dicas']) <= 3 and all(d['texto'] for d in dados['dicas']), dados['dicas'])
from impulso.models import Meta  # noqa: E402
from django.core.cache import cache  # noqa: E402
Meta.objects.create(titulo='ZZ atrasada', colaborador=ana, gestor=chefe, prazo=timezone.localdate() - timedelta(days=2),
                    aprovacao=Meta.Aprovacao.APROVADA, status=Meta.Status.A_FAZER)
cache.delete(f'impulso:resumo_home:v1:{ana.pk}')
dados = resumo(ana)
t('meta atrasada vira a primeira dica, com link do Kanban', dados['dicas'] and 'atrasada' in dados['dicas'][0]['texto']
  and dados['dicas'][0]['url'] == '/impulso/metas/', dados['dicas'][:1])
with mock.patch('impulso.resumo_home.contexto_do_cartao', side_effect=RuntimeError('quebrou')):
    t('erro no cálculo não derruba: responde vazio', ca.get('/impulso/resumo-home/').status_code == 204)

print('Feed: publicar')
r = ca.post('/social/publicar/', {'midia': foto(), 'legenda': 'ZZ primeira foto'})
j = r.json()
p1 = Post.objects.filter(autor=ana).first()
t('foto publicada com a medida lida da imagem', r.status_code == 200 and j['ok'] and p1 and p1.tipo == 'IMAGEM'
  and (p1.largura, p1.altura) == (800, 600), j)
t('a resposta traz o post pronto para a tela', 'data-sf-post="%s"' % p1.pk in j['html'] and 'ZZ primeira foto' in j['html'])
r = cb.post('/social/publicar/', {'midia': video(), 'largura': '1080', 'altura': '1920'})
p2 = Post.objects.filter(autor=bia).first()
t('vídeo publicado sem descrição, com a medida mandada pela tela', r.status_code == 200 and p2.tipo == 'VIDEO'
  and p2.legenda == '' and (p2.largura, p2.altura) == (1080, 1920))
t('vídeo em pé fica no limite 4:5 na tela', p2.proporcao == 125.0, p2.proporcao)
r = ca.post('/social/publicar/', {'midia': SimpleUploadedFile('virus.exe', b'MZ' + b'0' * 50)})
t('o que não é foto nem vídeo é recusado', r.status_code == 400 and 'Envie uma foto' in r.json()['erro'])
r = ca.post('/social/publicar/', {'midia': SimpleUploadedFile('falsa.jpg', b'nao sou imagem' * 10, content_type='image/jpeg')})
t('foto falsa (extensão de imagem, conteúdo não) é recusada', r.status_code == 400 and 'abrir essa foto' in r.json()['erro'])
r = ca.post('/social/publicar/', {'midia': SimpleUploadedFile('falso.mp4', b'x' * 100, content_type='video/mp4')})
t('vídeo falso é recusado', r.status_code == 400)
with mock.patch('social.servicos.LIMITE_IMAGEM', 10):
    r = ca.post('/social/publicar/', {'midia': foto()})
t('foto acima do limite é recusada com o limite no aviso', r.status_code == 400 and 'limite' in r.json()['erro'])
t('sem arquivo: pede a foto', ca.post('/social/publicar/', {}).status_code == 400)
t('GET não publica', ca.get('/social/publicar/').status_code == 405)

print('Feed: curtir e comentar')
r = cb.post(f'/social/{p1.pk}/curtir/')
t('curtir soma 1', r.json() == {'ok': True, 'curti': True, 'total': 1})
r = cb.post(f'/social/{p1.pk}/curtir/')
t('curtir de novo descurte', r.json() == {'ok': True, 'curti': False, 'total': 0})
cb.post(f'/social/{p1.pk}/curtir/'); cs.post(f'/social/{p1.pk}/curtir/')
p1.refresh_from_db()
t('o total fica no post', p1.total_curtidas == 2)
avisos_antes = Notification.objects.filter(user=ana).count()
for n in range(5):
    r = cb.post(f'/social/{p1.pk}/comentar/', json.dumps({'texto': f'ZZC{n} que foto'}), content_type='application/json')
t('comentar devolve o comentário pronto e o total', r.status_code == 200 and 'ZZC4' in r.json()['html'] and r.json()['total'] == 5)
t('o dono do post recebe aviso a cada comentário de outra pessoa',
  Notification.objects.filter(user=ana).count() == avisos_antes + 5
  and Notification.objects.filter(user=ana, related_url=f'/social/{p1.pk}/').exists())
ca.post(f'/social/{p1.pk}/comentar/', json.dumps({'texto': 'ZZ eu mesma'}), content_type='application/json')
t('o próprio comentário não avisa o dono', Notification.objects.filter(user=ana).count() == avisos_antes + 5)
r = cb.post(f'/social/{p1.pk}/comentar/', json.dumps({'texto': '   '}), content_type='application/json')
t('comentário vazio é recusado', r.status_code == 400)

print('Feed: o que aparece')
h = ca.get('/social/').content.decode()
corpo = h[h.find(f'data-sf-post="{p1.pk}"'):]
corpo = corpo[:corpo.find('</article>')]
t('no feed, só os 3 últimos comentários do post, em ordem', 'ZZC2' not in corpo and corpo.find('ZZC3') < corpo.find('ZZC4') < corpo.find('ZZ eu mesma'))
t('e o "Ver todos os 6 comentários"', 'Ver todos os 6 comentários' in corpo)
t('a curtida da pessoa vem marcada e o total aparece', '<b>2</b> curtidas' in corpo)
hb = cb.get('/social/').content.decode()
corpo_b = hb[hb.find(f'data-sf-post="{p1.pk}"'):]
corpo_b = corpo_b[:corpo_b.find('</article>')]
t('quem curtiu vê o coração cheio', 'sf-acao curti' in corpo_b and 'sf-acao curti' not in corpo)
t('o mais novo vem primeiro', h.find(f'data-sf-post="{p2.pk}"') < h.find(f'data-sf-post="{p1.pk}"'))
r = ca.get(f'/social/{p1.pk}/comentarios/')
t('"ver todos" traz os 6', r.status_code == 200 and all(f'ZZC{n}' in r.content.decode() for n in range(5)))
r = ca.get(f'/social/{p1.pk}/')
t('a página do post (link do aviso) abre com todos os comentários', r.status_code == 200 and 'ZZC0' in r.content.decode()
  and 'Ver todos os' not in r.content.decode())
for n in range(10):
    Post.objects.create(autor=bia, midia='social/zz.jpg', tipo='IMAGEM', legenda=f'ZZLOTE{n}')
r = ca.get('/social/mais/', {'antes': Post.objects.order_by('-id').first().pk})
j = r.json()
t('página seguinte pelo id: 8 e avisa que tem mais', j['ok'] and j['html'].count('data-sf-post=') == 8 and j['tem_mais'])
j2 = ca.get('/social/mais/', {'antes': j['ultimo']}).json()
t('a última página fecha sem repetir', j2['html'].count('data-sf-post=') == 3 and not j2['tem_mais']
  and 'ZZLOTE9' not in j2['html'])
from django.db import connection  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402
with CaptureQueriesContext(connection) as q:
    ca.get('/social/mais/')
t('a página do feed não consulta post por post', len([x for x in q.captured_queries if 'social_' in x['sql']]) <= 3,
  len([x for x in q.captured_queries if 'social_' in x['sql']]))

print('Feed: apagar')
c_bia = Comentario.objects.filter(post=p1, autor=bia).first()
r = cb.post(f'/social/{p1.pk}/apagar/')
t('colega não apaga o post dos outros', r.status_code == 403 and Post.objects.get(pk=p1.pk).ativo)
r = ca.post(f'/social/comentario/{c_bia.pk}/apagar/')
t('o dono do post apaga comentário de outra pessoa e o total desce', r.json()['ok'] and r.json()['total'] == 5
  and not Comentario.objects.get(pk=c_bia.pk).ativo)
c_ana = Comentario.objects.filter(post=p1, autor=ana).first()
r = cb.post(f'/social/comentario/{c_ana.pk}/apagar/')
t('quem não é dono nem autor não apaga comentário', r.status_code == 403)
r = cs.post(f'/social/{p2.pk}/apagar/')
t('SUPERADMIN apaga qualquer post (fica guardado como apagado)', r.json()['ok'] and not Post.objects.get(pk=p2.pk).ativo
  and Post.objects.get(pk=p2.pk).apagado_por_id == chefe.pk)
t('post apagado some do feed e não abre', f'data-sf-post="{p2.pk}"' not in ca.get('/social/').content.decode()
  and ca.get(f'/social/{p2.pk}/').status_code == 404)
r = ca.post(f'/social/{p1.pk}/apagar/')
t('quem publicou apaga o próprio post', r.json()['ok'] and not Post.objects.get(pk=p1.pk).ativo)

print('Home e menu')
h = ca.get('/').content.decode()
t('a home tem a seção do feed com a caixa de publicar', 'id="feed-da-rede"' in h and 'data-sf-composer' in h
  and 'Compartilhe uma foto ou um vídeo' in h)
t('a home mostra só 3 posts e o "Ver mais"', h.count('data-sf-post=') == 3 and 'data-sf-mais' in h)
t('câmera de foto, de vídeo e galeria', 'accept="image/*" capture="environment"' in h and 'accept="video/*" capture="environment"' in h
  and 'accept="image/*,video/*"' in h)
t('o menu tem o Feed da rede', 'href="/social/"' in h and 'Feed da rede' in h)
t('anônimo não vê o feed', Client().get('/social/').status_code == 302)

print(f'\n{OK} OK, {FALHAS} falha(s)')
sys.exit(1 if FALHAS else 0)
