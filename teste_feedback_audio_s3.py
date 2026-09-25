"""Feedback: o áudio precisa ir para o S3 (MinIO) e ficar guardado lá.

Pedido: "em /feedback — não está salvando o áudio no S3, deve salvar e guardar
corretamente".

O que estava errado: o Django 5.1 removeu ``DEFAULT_FILE_STORAGE``, e o
settings ainda dependia dele. O Django ignorava a linha em silêncio, o storage
padrão voltava a ser o disco do container e o `audio_file` — o único campo de
arquivo do portal sem storage explícito, junto com o anexo do chat da D-1 — era
gravado lá. O banco guardava o caminho, o MinIO não tinha o arquivo (0 objetos
em media/feedback) e o player do /feedback apontava para o nada. No deploy
seguinte, o áudio sumia de vez.

O que este teste cobre:

- o storage do campo e o storage padrão do projeto apontando para o MinIO;
- gravar um feedback com áudio pela tela e o objeto aparecendo no bucket, com
  os mesmos bytes e a URL do MinIO;
- nada escrito no disco local;
- a transcrição lendo o áudio de volta do S3 (Whisper é dublê);
- a tela de criar (formulário multipart, campo 'audio') e a de detalhe (player
  apontando para o MinIO).

O que sobe para o MinIO neste teste é apagado no fim. Banco em transação
desfeita; nenhuma chamada de IA sai daqui.
"""
import os
import sys
from datetime import date
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fb-audio'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fb-audio-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client

from feedback import ai
from feedback.models import Feedback
from users.models import Sector

User = get_user_model()
ok = fail = 0
subidos = []          # o que este teste colocou no MinIO


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def storage_real(st):
    """O storage de verdade por trás do proxy preguiçoso do Django."""
    st.exists('zz-so-para-materializar')
    return getattr(st, '_wrapped', st)


# Um "áudio" pequeno, só para viajar até o MinIO e voltar igual.
BYTES = b'\x1aE\xdf\xa3zz-feedback-audio-de-teste' + bytes(range(256)) * 4


class WhisperFalso:
    """Dublê do cliente da OpenAI: guarda o que recebeu e devolve um texto."""

    recebido = None

    def __init__(self, *a, **kw):
        pass

    @property
    def audio(self):
        return self

    @property
    def transcriptions(self):
        return self

    def create(self, model=None, file=None, language=None, response_format=None):
        WhisperFalso.recebido = file.read()
        return 'transcrição de teste'


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== ONDE O ARQUIVO VAI PARAR ==')
    padrao = storage_real(default_storage)
    campo = storage_real(Feedback._meta.get_field('audio_file').storage)
    t('o storage padrão do projeto é o MinIO',
      type(padrao).__name__ == 'MediaStorage', type(padrao).__name__)
    t('e o campo do áudio também', type(campo).__name__ == 'MediaStorage', type(campo).__name__)
    t('na mesma pasta dos outros anexos', getattr(campo, 'location', '') == 'media',
      getattr(campo, 'location', ''))
    t('e no bucket configurado',
      getattr(campo, 'bucket_name', '') == settings.AWS_STORAGE_BUCKET_NAME)
    t('o settings não depende mais do DEFAULT_FILE_STORAGE (que o Django 5.1 removeu)',
      not hasattr(settings, 'DEFAULT_FILE_STORAGE')
      and settings.STORAGES['default']['BACKEND'] == 'core.storage.MediaStorage',
      settings.STORAGES['default']['BACKEND'])
    t('e o disco local tem um caminho de verdade, se um dia for usado',
      str(getattr(settings, 'MEDIA_ROOT', '')).endswith('media'), settings.MEDIA_ROOT)

    print('\n== GRAVAR UM FEEDBACK COM ÁUDIO ==')
    setor = Sector.objects.create(name='ZZ Setor do áudio')
    chefe = User.objects.create_user(
        username='zzfb.chefe', email='zzfb.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    colab = User.objects.create_user(
        username='zzfb.colab', email='zzfb.colab@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Colaborador', sector=setor)

    c = Client()
    c.force_login(chefe)

    audio = SimpleUploadedFile('feedback_audio.webm', BYTES, content_type='audio/webm')
    dados = {
        'evaluatee': colab.id,
        'data': date(2026, 9, 25).isoformat(),
        'nome_colaborador': 'ZZ Colaborador',
        'setor_area': setor.name,
        'pontos_fortes': 'zz ponto forte',
        'audio_context': 'CONVERSA',
        'audio': audio,
    }
    # A tela chama a IA depois de salvar; aqui ela não sai do lugar.
    with mock.patch('feedback.views.transcribe_feedback_audio', return_value=''), \
         mock.patch('feedback.views.generate_ai_summary', return_value=''):
        r = c.post(f'/feedback/novo/?evaluatee={colab.id}', dados, follow=True)

    fb = Feedback.objects.filter(evaluatee=colab).order_by('-id').first()
    t('o feedback foi salvo', fb is not None and r.status_code == 200, r.status_code)
    t('com o áudio no campo', bool(fb and fb.audio_file), fb.audio_file if fb else None)
    if fb and fb.audio_file:
        subidos.append(fb.audio_file.name)

    nome = fb.audio_file.name
    t('o caminho é o da pasta do feedback', nome.startswith('feedback/audio/2026/09/'), nome)
    t('o arquivo existe mesmo no MinIO', fb.audio_file.storage.exists(nome))
    t('com o tamanho certo', fb.audio_file.storage.size(nome) == len(BYTES),
      fb.audio_file.storage.size(nome))

    with fb.audio_file.storage.open(nome, 'rb') as arq:
        voltou = arq.read()
    t('e os mesmos bytes que subiram', voltou == BYTES, (len(voltou), len(BYTES)))

    url = fb.audio_file.url
    t('a URL aponta para o MinIO, não para /media/',
      settings.AWS_S3_CUSTOM_DOMAIN in url or settings.AWS_S3_ENDPOINT_URL in url, url)
    t('e passa pela pasta media/feedback/audio', 'media/feedback/audio' in url, url)

    local = os.path.join(str(settings.MEDIA_ROOT), 'feedback')
    t('nada foi escrito no disco local', not os.path.exists(local), local)

    print('\n== A TRANSCRIÇÃO LÊ DO S3 ==')
    WhisperFalso.recebido = None
    with mock.patch.object(settings, 'OPENAI_API_KEY', 'zz-chave-de-teste'), \
         mock.patch('openai.OpenAI', WhisperFalso):
        texto = ai.transcribe_feedback_audio(fb, force=True)
    t('a transcrição roda com o arquivo que está no MinIO', texto == 'transcrição de teste', texto)
    t('e o Whisper recebeu exatamente aqueles bytes', WhisperFalso.recebido == BYTES,
      len(WhisperFalso.recebido or b''))
    fb.refresh_from_db()
    t('o texto fica guardado no feedback', fb.audio_transcription == 'transcrição de teste')
    t('com a hora da transcrição', fb.audio_transcribed_at is not None)

    print('\n== AS TELAS ==')
    html = c.get(f'/feedback/novo/?evaluatee={colab.id}').content.decode()
    t('a tela de criar manda o formulário como multipart',
      'enctype="multipart/form-data"' in html)
    t('e o campo do áudio se chama "audio"', 'name="audio"' in html)
    t('a gravação do navegador é anexada nesse campo',
      'dt.items.add(recordedFile)' in html and 'fileInput.files = dt.files' in html)

    html = c.get(f'/feedback/{fb.id}/').content.decode()
    t('o detalhe toca o áudio pela URL do MinIO', url in html, url[:60])
    t('e mostra a transcrição', 'transcrição de teste' in html)

    print('\n== O QUE MAIS DEPENDIA DISSO ==')
    outro = Feedback._meta.get_field('audio_file')
    t('o áudio continua opcional (feedback sem áudio salva igual)',
      outro.blank and outro.null)
    with mock.patch('feedback.views.transcribe_feedback_audio', return_value=''), \
         mock.patch('feedback.views.generate_ai_summary', return_value=''):
        r = c.post(f'/feedback/novo/?evaluatee={colab.id}',
                   {'evaluatee': colab.id, 'data': '2026-09-25', 'nome_colaborador': 'ZZ Sem áudio'},
                   follow=True)
    t('feedback sem áudio segue salvando', Feedback.objects.filter(
        evaluatee=colab, nome_colaborador='ZZ Sem áudio').exists())
finally:
    apagados = 0
    for nome in subidos:
        try:
            default_storage.delete(nome)
            apagados += 1
        except Exception as exc:                                  # noqa: BLE001
            print(f'  ATENÇÃO: não deu para apagar {nome} do MinIO: {exc}')
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print(f'\nrollback: nada gravado no banco; {apagados} arquivo(s) de teste apagados do MinIO.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
