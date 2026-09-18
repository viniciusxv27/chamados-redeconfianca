"""Treinamentos: o envio de vídeo (até 500 MB) e a miniatura padrão da Rede Confiança.

Pedidos:
- /trainings/upload/ dava erro ao subir vídeo. A causa: depois de salvar, um sinal
  "movia" o vídeo no armazenamento — baixava o arquivo inteiro do MinIO para a memória
  (o django-storages não passa para o disco) e subia de novo; com vídeo grande o worker
  estourava a memória. Agora o vídeo sobe uma vez só, já na pasta do treinamento, a
  tela mostra o progresso de verdade e o servidor diz o que deu errado;
- /trainings/: quem não tem miniatura usa a padrão da Rede Confiança;
- "retire o tempo limite para subir vídeo": com o MinIO, o vídeo sobe do navegador
  direto para lá, em partes de 8 MB (trainings/envio_direto.py) — nenhuma requisição
  longa passa pelo gunicorn, então não há o limite de 30 min do worker.

Nada vai para o MinIO: os dois campos de arquivo usam um armazenamento em memória que
conta quantas vezes cada arquivo foi gravado e lido, e o cliente S3 do envio direto é
um dublê (S3Falso) que grava nesse mesmo armazenamento — sem rede. Tudo roda numa
transação desfeita. Essas telas não mandam aviso nenhum.
"""
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from botocore.exceptions import ClientError
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from PIL import Image

import trainings.envio_direto as envio_direto
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
    """Em memória, contando gravações e leituras (a cópia antiga lia o vídeo de volta).

    Tem também o que o envio direto lê do armazenamento do MinIO: bucket, endereços,
    chave completa (com a pasta "trainings/" do TrainingStorage) e parâmetros de gravação.
    """
    bucket_name = 'chamados-teste'
    custom_domain = 'minio.teste/chamados-teste'
    endpoint_url = 'https://minio.teste'
    region_name = 'us-east-1'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gravados, self.lidos, self.falhar = [], [], False

    def _normalize_name(self, name):
        return f'trainings/{name}'

    def _get_write_parameters(self, name, content=None):
        return {'CacheControl': 'max-age=86400', 'ACL': 'public-read',
                'ContentType': mimetypes.guess_type(name)[0] or 'application/octet-stream'}

    def _save(self, name, content):
        if self.falhar:
            raise ConnectionError('armazenamento fora do ar')
        self.gravados.append(name)
        return super()._save(name, content)

    def _open(self, name, mode='rb'):
        self.lidos.append(name)
        return super()._open(name, mode)


class S3Falso:
    """O pedaço do boto3 que o envio direto usa. O "MinIO" é o ArmazenamentoContado: fechar
    o envio grava lá o vídeo juntado. `receber_parte` faz o papel do navegador (PUT na URL)."""

    def __init__(self, armazenamento):
        self.armazenamento = armazenamento
        self.abertos, self.criados, self.abortados, self.apagados = {}, [], [], []
        self.endereco = None

    @staticmethod
    def _erro(codigo, operacao):
        return ClientError({'Error': {'Code': codigo, 'Message': codigo}}, operacao)

    def _nome(self, chave):
        assert chave.startswith('trainings/'), chave
        return chave[len('trainings/'):]

    def create_multipart_upload(self, Bucket, Key, **parametros):
        upload_id = f'envio-{len(self.criados) + 1}'
        self.criados.append((Key, parametros))
        self.abertos[upload_id] = {'chave': Key, 'partes': {}}
        return {'UploadId': upload_id}

    def generate_presigned_url(self, operacao, Params, ExpiresIn):
        assert operacao == 'upload_part'
        return (f'{self.endereco}/{Params["Bucket"]}/{Params["Key"]}?uploadId={Params["UploadId"]}'
                f'&partNumber={Params["PartNumber"]}&X-Amz-Expires={ExpiresIn}')

    def receber_parte(self, url, dados):
        consulta = parse_qs(urlparse(url).query)
        self.abertos[consulta['uploadId'][0]]['partes'][int(consulta['partNumber'][0])] = dados
        return f'"{hashlib.md5(dados).hexdigest()}"'

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        aberto = self.abertos.get(UploadId)
        if aberto is None or aberto['chave'] != Key:
            raise self._erro('NoSuchUpload', 'CompleteMultipartUpload')
        for parte in MultipartUpload['Parts']:
            dados = aberto['partes'].get(parte['PartNumber'])
            if dados is None or parte['ETag'] != f'"{hashlib.md5(dados).hexdigest()}"':
                raise self._erro('InvalidPart', 'CompleteMultipartUpload')
        del self.abertos[UploadId]
        juntado = b''.join(aberto['partes'][p['PartNumber']] for p in MultipartUpload['Parts'])
        self.armazenamento._save(self._nome(Key), ContentFile(juntado))

    def head_object(self, Bucket, Key):
        if not self.armazenamento.exists(self._nome(Key)):
            raise self._erro('404', 'HeadObject')
        # HEAD não baixa o arquivo; o size() do InMemoryStorage abre, então lê direto, sem contar leitura.
        return {'ContentLength': len(InMemoryStorage._open(self.armazenamento, self._nome(Key)).file.getvalue())}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        if self.abertos.pop(UploadId, None) is None:
            raise self._erro('NoSuchUpload', 'AbortMultipartUpload')
        self.abortados.append(UploadId)

    def delete_object(self, Bucket, Key):
        self.apagados.append(Key)
        self.armazenamento.delete(self._nome(Key))


def png(tamanho=(64, 36)):
    buf = io.BytesIO()
    Image.new('RGB', tamanho, (255, 107, 53)).save(buf, format='PNG')
    return buf.getvalue()


def video(nome='aula.mp4', conteudo=b'\x00\x00\x00\x18ftypmp42' + b'x' * 4000):
    return SimpleUploadedFile(nome, conteudo, content_type='video/mp4')


armazenamento = ArmazenamentoContado()
s3 = S3Falso(armazenamento)


def cliente_falso(storage, endereco=None):
    s3.endereco = endereco or storage.endpoint_url
    return s3


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(Training._meta.get_field('video_file'), 'storage', armazenamento), \
            mock.patch.object(Training._meta.get_field('thumbnail'), 'storage', armazenamento), \
            mock.patch.object(envio_direto, '_cliente', cliente_falso), \
            mock.patch.object(envio_direto, 'disponivel', lambda: True):
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
        t('com o MinIO, a tela sobe o vídeo direto, em partes, e avisa que não há tempo limite',
          'const envioDireto = true;' in html and "iniciar: '/trainings/upload/video/iniciar/'" in html
          and "concluir: '/trainings/upload/video/concluir/'" in html and "cancelar: '/trainings/upload/video/cancelar/'" in html
          and html.count('Sem tempo limite') == 2 and "xhr.open('PUT', parte.url)" in html)
        with mock.patch.object(envio_direto, 'disponivel', lambda: False):
            html_local = c.get('/trainings/upload/').content.decode()
        t('sem o MinIO, o vídeo vai no próprio formulário (e a tela não promete o que não faz)',
          'const envioDireto = false;' in html_local and 'Sem tempo limite' not in html_local
          and 'Não feche esta página até o envio terminar.' in html_local)
        node = shutil.which('node')
        script = next((s for s in re.findall(r'<script>(.*?)</script>', html, flags=re.S) if 'enviarDireto' in s), '')
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

        print('\n== ENVIO DIRETO AO MINIO, EM PARTES (SEM TEMPO LIMITE) ==')
        admin2 = User.objects.create_user(username='zztreino.admin2', email='zztreino.admin2@exemplo-teste.local',
                                          password='S3nha!teste', first_name='ZZTreino', last_name='Admin2',
                                          hierarchy='SUPERADMIN')
        c2 = Client()
        c2.force_login(admin2)

        def formulario(**extra):
            corpo = {'title': 'ZZ Direto', 'description': 'Descrição', 'category': str(categoria.pk),
                     'duration_seconds': '600', 'video_nome': 'Aula Grande.MP4', 'video_tamanho': str(20 * 1024 * 1024),
                     'video_tipo': 'video/mp4'}
            corpo.update(extra)
            return {k: v for k, v in corpo.items() if v is not None}

        def iniciar(cliente=c, **extra):
            resposta = cliente.post('/trainings/upload/video/iniciar/', formulario(**extra), **xhr)
            return resposta.status_code, resposta.json()

        status, dados = iniciar()
        chave, parametros = s3.criados[-1] if s3.criados else ('', {})
        t('abre o envio: uma URL assinada por parte de 8 MB (20 MB = 3 partes)', status == 200 and dados['ok']
          and dados['tamanho_parte'] == 8 * 1024 * 1024 and len(dados['urls']) == 3
          and all(f'partNumber={n}' in u for n, u in zip((1, 2, 3), dados['urls'])), (status, dados))
        t('as URLs valem 24 h (o MinIO descarta envio parado depois disso)', all('X-Amz-Expires=86400' in u for u in dados['urls']))
        t('o vídeo vai para trainings/videos/envios/, com a extensão em minúsculas',
          re.fullmatch(r'trainings/trainings/videos/envios/[0-9a-f]{32}\.mp4', chave) is not None, chave)
        t('público, com cache e o tipo de vídeo — como no envio pelo formulário',
          parametros == {'CacheControl': 'max-age=86400', 'ACL': 'public-read', 'ContentType': 'video/mp4'}, parametros)
        r = c.post('/trainings/upload/video/cancelar/', {'envio': dados['envio']}, **xhr)
        t('cancelar desfaz o envio no MinIO (as partes são descartadas)', r.status_code == 200 and r.json()['desfeito'] is True
          and s3.abortados == ['envio-1'] and not s3.abertos, (r.content[:200], s3.abortados))
        r = c.post('/trainings/upload/video/cancelar/', {'envio': dados['envio']}, **xhr)
        t('cancelar de novo não quebra (não há mais o que desfazer)', r.status_code == 200 and r.json()['desfeito'] is False)

        status, dados = iniciar(video_tipo='text/html')
        t('tipo declarado que não é de vídeo não vai para o MinIO (usa o da extensão)',
          status == 200 and s3.criados[-1][1]['ContentType'] == 'video/mp4', s3.criados[-1][1])
        c.post('/trainings/upload/video/cancelar/', {'envio': dados['envio']}, **xhr)

        print('\n  -- o formulário é conferido ANTES de o vídeo subir --')
        criados_antes = len(s3.criados)
        t('sem título', iniciar(title='') == (400, {'ok': False, 'erro': 'Título é obrigatório.'}))
        t('sem vídeo', iniciar(video_nome='') == (400, {'ok': False, 'erro': 'Arquivo de vídeo é obrigatório.'}))
        status, dados = iniciar(video_nome='planilha.xlsx')
        t('formato que não é vídeo', status == 400 and 'Formato XLSX não aceito' in dados['erro'], dados)
        status, dados = iniciar(video_tamanho=str(501 * 1024 * 1024))
        t('acima de 500 MB', status == 400 and dados['erro'] == 'O vídeo tem 501 MB e o limite é 500 MB.', dados)
        status, dados = iniciar(video_tamanho='0')
        t('vídeo vazio', status == 400 and 'vazio' in dados['erro'], dados)
        status, dados = iniciar(video_tamanho='-5')
        t('tamanho que não é número', status == 400 and dados['erro'] == 'Arquivo de vídeo é obrigatório.', dados)
        status, dados = iniciar(thumbnail=SimpleUploadedFile('capa.png', b'<html>x</html>', 'image/png'))
        t('miniatura que não abre como imagem', status == 400 and 'miniatura precisa ser uma imagem' in dados['erro'], dados)
        status, dados = iniciar(category='999999999')
        t('categoria que não existe', (status, dados.get('erro')) == (400, 'Categoria inválida.'))
        t('nada disso abriu envio no MinIO', len(s3.criados) == criados_antes, len(s3.criados) - criados_antes)
        r = c_comum.post('/trainings/upload/video/iniciar/', formulario(), **xhr)
        t('quem não gerencia treinamentos não abre envio', r.status_code == 403 and len(s3.criados) == criados_antes)
        r = c.get('/trainings/upload/video/iniciar/')
        t('só por POST', r.status_code == 405)
        with mock.patch.object(envio_direto, 'disponivel', lambda: False):
            r = c.post('/trainings/upload/video/iniciar/', formulario(), **xhr)
        t('sem o MinIO, o envio direto não existe', r.status_code == 400 and 'MinIO' in r.json()['erro'])

        def falha_s3(*a, **k):
            raise ClientError({'Error': {'Code': 'ServiceUnavailable', 'Message': 'fora'}}, 'CreateMultipartUpload')
        with mock.patch.object(s3, 'create_multipart_upload', falha_s3):
            r = c.post('/trainings/upload/video/iniciar/', formulario(), **xhr)
        t('MinIO fora do ar: explica, sem erro 500', r.status_code == 502
          and r.json()['erro'] == telas.ERRO_ARMAZENAMENTO, r.content[:200])

        print('\n  -- as partes sobem, o envio fecha e o treinamento é gravado --')
        with mock.patch.object(envio_direto, 'TAMANHO_PARTE', 1000):
            conteudo = bytes(range(256)) * 17 + b'fim'          # 4355 bytes = 5 partes de 1000
            status, aberto = iniciar(video_tamanho=str(len(conteudo)))
        t('com partes de 1000 bytes, 4355 bytes = 5 URLs', status == 200 and len(aberto['urls']) == 5, (status, aberto))
        pedacos = [conteudo[i:i + 1000] for i in range(0, len(conteudo), 1000)]
        partes = [{'numero': n, 'etag': s3.receber_parte(url, pedaco)}
                  for n, (url, pedaco) in enumerate(zip(aberto['urls'], pedacos), start=1)]
        chave = s3.criados[-1][0]
        nome = chave[len('trainings/'):]

        def concluir(lista, envio=None, cliente=c):
            resposta = cliente.post('/trainings/upload/video/concluir/',
                                    {'envio': envio or aberto['envio'], 'partes': json.dumps(lista)}, **xhr)
            return resposta.status_code, resposta.json()

        t('faltando parte, não fecha', concluir(partes[:-1]) == (400, {'ok': False, 'erro': 'Faltou parte do vídeo. Envie o vídeo de novo.'}))
        status, dados = concluir([dict(p, etag='"errado"') if p['numero'] == 3 else p for p in partes])
        t('parte trocada no caminho (ETag diferente), o MinIO recusa e a tela explica',
          status == 400 and 'InvalidPart' in dados['erro'], dados)
        status, dados = concluir(partes, cliente=c2)
        t('outra pessoa não fecha o envio de quem abriu', status == 400 and 'não vale mais' in dados['erro'], dados)
        status, dados = concluir(partes, envio=aberto['envio'][:-3] + 'xyz')
        t('token adulterado não vale', status == 400 and 'não vale mais' in dados['erro'], dados)
        r = c.post('/trainings/upload/video/concluir/', {'envio': aberto['envio'], 'partes': '{nao é json'}, **xhr)
        t('lista de partes ilegível', r.status_code == 400 and 'incompletas' in r.json()['erro'])
        with mock.patch('django.core.signing.time.time', return_value=time.time() + 25 * 3600):
            try:
                envio_direto.concluir(admin, aberto['envio'], partes)
                expirou = ''
            except envio_direto.ErroEnvio as exc:
                expirou = str(exc)
        t('depois de 24 h o envio expira', 'passou de 24 horas' in expirou, expirou)

        status, fechado = concluir(list(reversed(partes)))
        t('fecha com todas as partes (em qualquer ordem): o vídeo fica inteiro no MinIO', status == 200 and fechado['ok']
          and armazenamento.exists(nome) and armazenamento.open(nome).read() == conteudo, (status, fechado))
        armazenamento.lidos.clear()
        status, repetido = concluir(partes)
        t('fechar de novo (a resposta do primeiro se perdeu) dá certo do mesmo jeito', status == 200 and repetido['ok'], repetido)

        r = c.post('/trainings/upload/video/concluir/', {'envio': fechado['envio'], 'partes': json.dumps(partes)}, **xhr)
        t('o token "pronto" não serve para fechar de novo', r.status_code == 400)

        gravados_antes = list(armazenamento.gravados)
        r = c.post('/trainings/upload/', {'title': 'ZZ Direto', 'description': 'Descrição', 'category': str(categoria.pk),
                                         'duration_seconds': '600', 'video_envio': fechado['envio'],
                                         'thumbnail': SimpleUploadedFile('capa.png', png(), 'image/png')}, **xhr)
        direto = Training.objects.filter(title='ZZ Direto').first()
        t('grava o treinamento apontando para o vídeo que já está no MinIO', r.status_code == 200 and r.json()['ok']
          and direto and r.json()['redirect'] == f'/trainings/{direto.pk}/' and direto.video_file.name == nome
          and direto.file_size == len(conteudo) and direto.duration_seconds == 600 and direto.category_id == categoria.pk
          and direto.uploaded_by_id == admin.pk, r.content[:300])
        t('sem subir o vídeo de novo nem ler de volta: só a miniatura foi gravada',
          armazenamento.gravados[len(gravados_antes):] == [direto.thumbnail.name] and armazenamento.lidos == [],
          (armazenamento.gravados[len(gravados_antes):], armazenamento.lidos))
        t('a página do treinamento toca o vídeo do MinIO', direto.video_file.url in c.get(f'/trainings/{direto.pk}/').content.decode())

        antes = Training.objects.count()
        r = c.post('/trainings/upload/', {'title': 'ZZ Direto', 'description': 'Descrição', 'video_envio': fechado['envio']}, **xhr)
        t('o mesmo envio repetido (resposta perdida) leva ao treinamento já gravado, sem duplicar',
          r.status_code == 200 and r.json()['redirect'] == f'/trainings/{direto.pk}/' and Training.objects.count() == antes)
        r = c2.post('/trainings/upload/', {'title': 'ZZ Outro', 'description': 'x', 'video_envio': fechado['envio']}, **xhr)
        t('outra pessoa não usa o vídeo de quem enviou', r.status_code == 400 and 'não vale mais' in r.json()['erro']
          and r.json()['refazer_video'] is True and Training.objects.count() == antes)
        r = c.post('/trainings/upload/video/cancelar/', {'envio': fechado['envio']}, **xhr)
        t('cancelar um vídeo já publicado não apaga nada', r.status_code == 200 and r.json()['desfeito'] is False
          and armazenamento.exists(nome))

        print('\n  -- quando algo falha depois de o vídeo subir --')
        with mock.patch.object(envio_direto, 'TAMANHO_PARTE', 1000):
            status, aberto = iniciar(title='ZZ Direto 2', video_tamanho='2500')
        partes = [{'numero': n, 'etag': s3.receber_parte(url, b'v' * tamanho)}
                  for n, (url, tamanho) in enumerate(zip(aberto['urls'], (1000, 1000, 500)), start=1)]
        status, fechado = concluir(partes)
        nome2 = s3.criados[-1][0][len('trainings/'):]
        armazenamento.falhar = True
        r = c.post('/trainings/upload/', {'title': 'ZZ Direto 2', 'description': 'Descrição', 'video_envio': fechado['envio'],
                                         'thumbnail': SimpleUploadedFile('capa.png', png(), 'image/png')}, **xhr)
        armazenamento.falhar = False
        t('a miniatura não subiu: explica, não grava pela metade e o vídeo continua lá para a nova tentativa',
          r.status_code == 500 and 'não precisa subir outra vez' in r.json()['erro']
          and not Training.objects.filter(title='ZZ Direto 2').exists() and armazenamento.exists(nome2), r.content[:300])
        r = c.post('/trainings/upload/', {'title': 'ZZ Direto 2', 'description': 'Descrição', 'video_envio': fechado['envio']}, **xhr)
        t('e a nova tentativa grava, com o mesmo vídeo', r.status_code == 200
          and Training.objects.filter(title='ZZ Direto 2', video_file=nome2).exists(), r.content[:300])

        with mock.patch.object(envio_direto, 'TAMANHO_PARTE', 1000):
            status, aberto = iniciar(title='ZZ Direto 3', video_tamanho='1500')
        partes = [{'numero': 1, 'etag': s3.receber_parte(aberto['urls'][0], b'a' * 1000)},
                  {'numero': 2, 'etag': s3.receber_parte(aberto['urls'][1], b'a' * 400)}]
        status, dados = concluir(partes)
        nome3 = s3.criados[-1][0][len('trainings/'):]
        t('chegou menos do que o arquivo escolhido: recusa e apaga do MinIO', status == 400
          and 'tamanho diferente' in dados['erro'] and not armazenamento.exists(nome3), dados)

        with mock.patch.object(envio_direto, 'TAMANHO_PARTE', 1000):
            status, aberto = iniciar(title='ZZ Direto 4', video_tamanho='10')
        status, fechado = concluir([{'numero': 1, 'etag': s3.receber_parte(aberto['urls'][0], b'b' * 10)}])
        nome4 = s3.criados[-1][0][len('trainings/'):]
        r = c.post('/trainings/upload/video/cancelar/', {'envio': fechado['envio']}, **xhr)
        t('trocou de arquivo depois de o vídeo subir: cancelar apaga o vídeo que não foi usado',
          r.status_code == 200 and r.json()['desfeito'] is True and not armazenamento.exists(nome4))
        r = c.post('/trainings/upload/', {'title': 'ZZ Direto 4', 'description': 'x', 'video_envio': fechado['envio']}, **xhr)
        t('e aquele envio não grava mais treinamento', r.status_code == 400 and 'não está mais no armazenamento' in r.json()['erro']
          and r.json()['refazer_video'] is True)

        print('\n  -- endereço das URLs --')
        t('as URLs saem com o endereço público do MinIO (o das mídias)', all(u.startswith('https://minio.teste/chamados-teste/trainings/')
                                                                              for u in aberto['urls']), aberto['urls'][:1])
        with mock.patch.object(armazenamento, 'endpoint_url', 'http://minio-interno:9000', create=True):
            status, aberto = iniciar(title='ZZ Interno')
        t('mesmo se o portal falar com o MinIO pela rede interna', status == 200
          and aberto['urls'][0].startswith('https://minio.teste/chamados-teste/'), aberto.get('urls', [''])[:1])
        c.post('/trainings/upload/video/cancelar/', {'envio': aberto['envio']}, **xhr)
        publico = envio_direto._endereco_publico
        t('sem domínio próprio no formato <host>/<bucket>, fica o endereço configurado',
          publico(SimpleNamespace(custom_domain=None, bucket_name='b', endpoint_url='https://s3.x')) == 'https://s3.x'
          and publico(SimpleNamespace(custom_domain='cdn.x.com', bucket_name='b', endpoint_url='https://s3.x')) == 'https://s3.x'
          and publico(SimpleNamespace(custom_domain='a.com/pasta/b', bucket_name='b', endpoint_url='https://s3.x')) == 'https://s3.x'
          and publico(SimpleNamespace(custom_domain='minio.x.com/b', bucket_name='b', endpoint_url='http://minio:9000')) == 'https://minio.x.com')
        t('nenhum envio ficou aberto no MinIO', not s3.abertos, list(s3.abertos))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco (e nada subiu para o MinIO).')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
