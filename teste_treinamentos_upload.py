"""Treinamentos: o envio de vídeo (até 500 MB) e a miniatura padrão da Rede Confiança.

Pedidos:
- /trainings/upload/ dava erro ao subir vídeo. A causa: depois de salvar, um sinal
  "movia" o vídeo no armazenamento — baixava o arquivo inteiro do MinIO para a memória
  (o django-storages não passa para o disco) e subia de novo; com vídeo grande o worker
  estourava a memória. Agora o vídeo sobe uma vez só, já na pasta do treinamento, a
  tela mostra o progresso de verdade e o servidor diz o que deu errado;
- /trainings/: quem não tem miniatura usa a padrão da Rede Confiança.

Nada vai para o MinIO: os dois campos de arquivo usam um armazenamento em memória que
conta quantas vezes cada arquivo foi gravado e lido. Tudo roda numa transação desfeita.
Essas telas não mandam aviso nenhum.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from PIL import Image

import trainings.models as modelos
import trainings.views as telas
from trainings.models import MINIATURA_PADRAO, Training, TrainingCategory

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


class ArmazenamentoContado(InMemoryStorage):
    """Em memória, contando gravações e leituras (a cópia antiga lia o vídeo de volta)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gravados, self.lidos, self.falhar = [], [], False

    def _save(self, name, content):
        if self.falhar:
            raise ConnectionError('armazenamento fora do ar')
        self.gravados.append(name)
        return super()._save(name, content)

    def _open(self, name, mode='rb'):
        self.lidos.append(name)
        return super()._open(name, mode)


def png(tamanho=(64, 36)):
    buf = io.BytesIO()
    Image.new('RGB', tamanho, (255, 107, 53)).save(buf, format='PNG')
    return buf.getvalue()


def video(nome='aula.mp4', conteudo=b'\x00\x00\x00\x18ftypmp42' + b'x' * 4000):
    return SimpleUploadedFile(nome, conteudo, content_type='video/mp4')


armazenamento = ArmazenamentoContado()
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(Training._meta.get_field('video_file'), 'storage', armazenamento), \
            mock.patch.object(Training._meta.get_field('thumbnail'), 'storage', armazenamento):
        admin = User.objects.create_user(username='zztreino.admin', email='zztreino.admin@exemplo-teste.local',
                                         password='S3nha!teste', first_name='ZZTreino', last_name='Admin',
                                         hierarchy='SUPERADMIN')
        comum = User.objects.create_user(username='zztreino.comum', email='zztreino.comum@exemplo-teste.local',
                                         password='S3nha!teste', first_name='ZZTreino', last_name='Comum')
        categoria = TrainingCategory.objects.create(name='ZZ Categoria Teste Treinos')
        c = Client()
        c.force_login(admin)
        xhr = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

        print('== TELA DE ENVIO ==')
        r = c.get('/trainings/upload/')
        html = r.content.decode()
        t('abre, com o progresso de verdade (XMLHttpRequest) e o botão de cancelar', r.status_code == 200
          and 'xhr.upload.onprogress' in html and 'X-Requested-With' in html and 'id="cancelUpload"' in html
          and 'miniatura padrão da Rede Confiança' in html)
        t('o limite de 500 MB e os formatos vão para a tela', str(500 * 1024 * 1024) in html and "'mp4', 'avi'" in html)
        node = shutil.which('node')
        script = next((s for s in re.findall(r'<script>(.*?)</script>', html, flags=re.S) if 'xhr.upload.onprogress' in s), '')
        if node and script:
            with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as arquivo:
                arquivo.write(script)
            checagem = subprocess.run([node, '--check', arquivo.name], capture_output=True, text=True, timeout=30)
            os.unlink(arquivo.name)
            t('o JS da tela é válido (node --check)', checagem.returncode == 0, checagem.stderr[-300:])

        print('\n== ENVIO ==')
        t('não existe mais o sinal que copiava o vídeo depois de salvar',
          not hasattr(modelos, 'move_training_files_after_save'))
        dados = {'title': 'ZZ Treino de teste', 'description': 'Descrição', 'category': str(categoria.pk),
                 'duration_seconds': '125', 'video_file': video(), 'thumbnail': SimpleUploadedFile('capa.png', png(), 'image/png')}
        r = c.post('/trainings/upload/', dados, **xhr)
        treino = Training.objects.filter(title='ZZ Treino de teste').first()
        t('responde em JSON com o endereço do treinamento', r.status_code == 200 and r.json().get('ok') is True
          and treino and r.json().get('redirect') == f'/trainings/{treino.pk}/', r.content[:200])
        t('o vídeo e a miniatura sobem uma vez só, já na pasta do treinamento',
          treino and treino.video_file.name.startswith(f'trainings/videos/{treino.pk}/')
          and treino.thumbnail.name.startswith(f'trainings/thumbnails/{treino.pk}/')
          and armazenamento.gravados == [treino.video_file.name, treino.thumbnail.name], armazenamento.gravados)
        t('e nada é lido de volta do armazenamento (sem a cópia que estourava a memória)', armazenamento.lidos == [],
          armazenamento.lidos)
        t('guarda o tamanho, a duração, a categoria e quem enviou', treino and treino.file_size == dados['video_file'].size
          and treino.duration_seconds == 125 and treino.category_id == categoria.pk and treino.uploaded_by_id == admin.pk)
        r = c.post('/trainings/upload/', {'title': 'ZZ Sem JS', 'description': 'Descrição', 'video_file': video('b.webm')})
        sem_js = Training.objects.filter(title='ZZ Sem JS').first()
        t('sem JavaScript, o formulário comum continua indo para o treinamento', r.status_code == 302 and sem_js
          and r['Location'] == f'/trainings/{sem_js.pk}/' and not sem_js.thumbnail)

        print('\n== O QUE O SERVIDOR RECUSA (E EXPLICA) ==')
        antes = Training.objects.count()

        def recusa(**extra):
            corpo = {'title': 'ZZ Recusado', 'description': 'Descrição', 'video_file': video()}
            corpo.update(extra)
            corpo = {k: v for k, v in corpo.items() if v is not None}
            resposta = c.post('/trainings/upload/', corpo, **xhr)
            return resposta.status_code, (resposta.json().get('erro') if resposta['Content-Type'].startswith('application/json') else '')

        t('sem título', recusa(title='') == (400, 'Título é obrigatório.'))
        t('sem vídeo', recusa(video_file=None) == (400, 'Arquivo de vídeo é obrigatório.'))
        status, erro = recusa(video_file=video('planilha.xlsx'))
        t('formato que não é vídeo', status == 400 and 'Formato XLSX não aceito' in erro, erro)
        status, erro = recusa(video_file=video('vazio.mp4', b''))
        t('vídeo vazio', status == 400 and 'vazio' in erro, erro)
        with mock.patch.object(telas, 'VIDEO_MAX_BYTES', 1000):
            status, erro = recusa()
        t('acima do limite (500 MB)', status == 400 and 'o limite é 500 MB' in erro, erro)
        status, erro = recusa(thumbnail=SimpleUploadedFile('capa.png', b'<html>nao sou imagem</html>', 'image/png'))
        t('miniatura que não abre como imagem', status == 400 and 'miniatura precisa ser uma imagem' in erro, erro)
        status, erro = recusa(category='999999999')
        t('categoria que não existe', (status, erro) == (400, 'Categoria inválida.'))
        t('nada disso gravou treinamento nem arquivo', Training.objects.count() == antes
          and len(armazenamento.gravados) == 3, armazenamento.gravados)

        armazenamento.falhar = True
        status, erro = recusa(title='ZZ Armazenamento fora')
        armazenamento.falhar = False
        t('armazenamento fora do ar: explica e não deixa treinamento pela metade', status == 500
          and 'não foi guardado' in erro and not Training.objects.filter(title='ZZ Armazenamento fora').exists(), (status, erro))

        c_comum = Client()
        c_comum.force_login(comum)
        r = c_comum.post('/trainings/upload/', {'title': 'ZZ Intruso', 'description': 'x', 'video_file': video()}, **xhr)
        t('quem não gerencia treinamentos não envia', r.status_code == 302 and not Training.objects.filter(title='ZZ Intruso').exists())

        print('\n== MINIATURA PADRÃO ==')
        caminho = os.path.join(settings.BASE_DIR, 'static', MINIATURA_PADRAO)
        with Image.open(caminho) as imagem:
            t('a miniatura padrão existe (16:9, 1280×720)', imagem.size == (1280, 720) and imagem.format == 'JPEG')
        t('sem miniatura, o treinamento usa a padrão; com miniatura, a dele',
          sem_js.get_miniatura_url().endswith(MINIATURA_PADRAO) and treino.get_miniatura_url() == treino.thumbnail.url)
        html = c.get('/trainings/', {'search': 'ZZ '}).content.decode()
        t('a lista mostra a padrão em quem não tem e a própria em quem tem',
          MINIATURA_PADRAO in html and treino.thumbnail.url in html and 'fa-play text-4xl text-gray-400' not in html)
        html = c.get('/trainings/manage/', {'search': 'ZZ'}).content.decode()
        t('a gestão também', MINIATURA_PADRAO in html and 'fa-play text-gray-400' not in html)
        html = c.get(f'/trainings/{sem_js.pk}/').content.decode()
        t('e o vídeo do treinamento abre com ela de capa', f'poster="{sem_js.get_miniatura_url()}"' in html)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco (e nada subiu para o MinIO).')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
