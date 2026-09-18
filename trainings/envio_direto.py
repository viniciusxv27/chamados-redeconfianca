"""Envio do vídeo de treinamento direto do navegador para o MinIO, em partes.

Por quê: com o vídeo inteiro numa requisição só, o worker do gunicorn fica preso
durante todo o envio e é morto quando passa do tempo limite (30 min) — com vídeo
grande e internet de loja, o envio caía. Em partes de 8 MB, cada requisição é curta
e vai direto ao MinIO: o envio leva o tempo que precisar e, se a internet cair, a
tela continua de onde parou.

O portal só abre o envio (assinando a URL de cada parte), fecha no fim conferindo o
tamanho e depois grava o treinamento apontando para o arquivo que já está lá.

Quem guarda o andamento é um token assinado (não a sessão: com
SESSION_SAVE_EVERY_REQUEST, qualquer requisição paralela da mesma pessoa regravaria
a sessão antiga por cima). O token vale só para quem abriu o envio e só na fase dele:
"aberto" (partes subindo) ou "pronto" (vídeo inteiro no MinIO, falta gravar).
"""
import math
import re
import uuid

from django.conf import settings
from django.core import signing

TAMANHO_PARTE = 8 * 1024 * 1024          # o S3 pede no mínimo 5 MB por parte (menos a última)
VALIDADE = 24 * 3600                     # o MinIO descarta sozinho envio em partes não concluído em 24 h
SAL = 'trainings.envio_direto'
TIPO_DE_VIDEO = re.compile(r'^video/[\w.+-]{1,60}$')


class ErroEnvio(Exception):
    """O envio pedido não vale (de outra pessoa, expirado, faltou parte, tamanho diferente...)."""


def disponivel():
    """Só com o MinIO (USE_S3): no armazenamento local o vídeo continua indo pelo formulário."""
    return bool(getattr(settings, 'USE_S3', False))


def _armazenamento():
    from .models import Training

    return Training._meta.get_field('video_file').storage


def _cliente(storage, endereco=None):
    """Cliente próprio, com SigV4 e endereço por caminho: o do django-storages assina as
    URLs em SigV2 e o MinIO recusa (403)."""
    from botocore.config import Config

    return storage._create_session().client(
        's3', region_name=storage.region_name, endpoint_url=endereco or storage.endpoint_url,
        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))


def _endereco_publico(storage):
    """O endereço do MinIO que o navegador alcança, para assinar as URLs das partes.

    Quem usa essas URLs é o navegador, não o servidor. Se o portal falar com o MinIO por
    um endereço interno (rede do Docker), elas precisam sair com o público — o mesmo das
    mídias: AWS_S3_CUSTOM_DOMAIN = "<host>/<bucket>".
    """
    dominio = (storage.custom_domain or '').strip('/')
    sufixo = f'/{storage.bucket_name}'
    if dominio.endswith(sufixo) and '/' not in dominio[:-len(sufixo)]:
        return f'https://{dominio[:-len(sufixo)]}'
    return storage.endpoint_url


def _chave(storage, nome):
    from storages.utils import clean_name

    return storage._normalize_name(clean_name(nome))


def _codigo(erro):
    return getattr(erro, 'response', {}).get('Error', {}).get('Code', '')


def _assinar(**dados):
    return signing.dumps(dados, salt=SAL, compress=True)


def _abrir(token, usuario, fase=None):
    invalido = 'Esse envio de vídeo não vale mais. Recarregue a página e envie o vídeo de novo.'
    try:
        dados = signing.loads(token or '', salt=SAL, max_age=VALIDADE)
    except signing.SignatureExpired:
        raise ErroEnvio('O envio do vídeo passou de 24 horas e expirou. Envie o vídeo de novo.') from None
    except signing.BadSignature:
        raise ErroEnvio(invalido) from None
    if dados.get('usuario') != usuario.pk or (fase and dados.get('fase') != fase):
        raise ErroEnvio(invalido)
    return dados


def iniciar(usuario, extensao, tamanho, tipo=''):
    """Abre o envio em partes: {'envio': token, 'tamanho_parte', 'urls': [uma URL assinada por parte]}.

    Formato e tamanho já foram conferidos pela tela (_erro_do_envio).
    """
    storage = _armazenamento()
    cliente = _cliente(storage)
    nome = f'trainings/videos/envios/{uuid.uuid4().hex}.{extensao}'
    chave = _chave(storage, nome)
    parametros = storage._get_write_parameters(nome)          # ACL pública, Cache-Control e tipo, como no envio comum
    if TIPO_DE_VIDEO.match(tipo or ''):
        parametros['ContentType'] = tipo                     # o tipo que o navegador declarou, se for de vídeo
    upload_id = cliente.create_multipart_upload(Bucket=storage.bucket_name, Key=chave, **parametros)['UploadId']
    partes = max(1, math.ceil(tamanho / TAMANHO_PARTE))
    publico = _endereco_publico(storage)
    assinante = cliente if publico == storage.endpoint_url else _cliente(storage, publico)
    urls = [assinante.generate_presigned_url(
        'upload_part', Params={'Bucket': storage.bucket_name, 'Key': chave, 'UploadId': upload_id, 'PartNumber': numero},
        ExpiresIn=VALIDADE) for numero in range(1, partes + 1)]
    envio = _assinar(usuario=usuario.pk, fase='aberto', upload_id=upload_id, nome=nome, tamanho=tamanho, partes=partes)
    return {'envio': envio, 'tamanho_parte': TAMANHO_PARTE, 'urls': urls}


def concluir(usuario, token, partes):
    """Junta as partes no MinIO e confere o tamanho. Devolve o token "pronto" do vídeo."""
    from botocore.exceptions import ClientError

    dados = _abrir(token, usuario, 'aberto')
    try:
        lista = sorted(({'PartNumber': int(p['numero']), 'ETag': str(p['etag'])} for p in partes),
                       key=lambda p: p['PartNumber'])
    except (KeyError, TypeError, ValueError):
        raise ErroEnvio('As partes do vídeo chegaram incompletas. Envie o vídeo de novo.') from None
    if [p['PartNumber'] for p in lista] != list(range(1, dados['partes'] + 1)) or not all(p['ETag'] for p in lista):
        raise ErroEnvio('Faltou parte do vídeo. Envie o vídeo de novo.')

    storage = _armazenamento()
    cliente = _cliente(storage)
    chave = _chave(storage, dados['nome'])
    try:
        cliente.complete_multipart_upload(Bucket=storage.bucket_name, Key=chave, UploadId=dados['upload_id'],
                                          MultipartUpload={'Parts': lista})
    except ClientError as erro:
        # Pedido repetido (a resposta do primeiro se perdeu na rede): o envio já foi fechado.
        if _codigo(erro) != 'NoSuchUpload' or _tamanho(cliente, storage, chave) != dados['tamanho']:
            raise ErroEnvio(f'O armazenamento não aceitou as partes do vídeo ({_codigo(erro) or erro}). '
                            'Envie o vídeo de novo.') from None
    tamanho = _tamanho(cliente, storage, chave)
    if tamanho != dados['tamanho']:
        cliente.delete_object(Bucket=storage.bucket_name, Key=chave)
        raise ErroEnvio('O vídeo chegou com um tamanho diferente do arquivo escolhido. Envie o vídeo de novo.')
    return _assinar(usuario=usuario.pk, fase='pronto', nome=dados['nome'], tamanho=tamanho)


def _tamanho(cliente, storage, chave):
    from botocore.exceptions import ClientError

    try:
        return cliente.head_object(Bucket=storage.bucket_name, Key=chave)['ContentLength']
    except ClientError:
        return None


def usar(usuario, token):
    """(nome, tamanho) do vídeo "pronto" desta pessoa, para gravar o treinamento."""
    dados = _abrir(token, usuario, 'pronto')
    if not _armazenamento().exists(dados['nome']):
        raise ErroEnvio('O vídeo enviado não está mais no armazenamento. Envie o vídeo de novo.')
    return dados['nome'], dados['tamanho']


def cancelar(usuario, token):
    """Desiste do envio: o MinIO descarta as partes — ou o vídeo inteiro, se já tinha
    subido e nenhum treinamento usa. Devolve se havia o que desfazer."""
    from botocore.exceptions import ClientError

    from .models import Training

    dados = _abrir(token, usuario)
    storage = _armazenamento()
    cliente = _cliente(storage)
    chave = _chave(storage, dados['nome'])
    if dados.get('fase') == 'aberto':
        try:
            cliente.abort_multipart_upload(Bucket=storage.bucket_name, Key=chave, UploadId=dados['upload_id'])
            return True
        except ClientError as erro:
            if _codigo(erro) != 'NoSuchUpload':
                raise
        # Já tinha sido fechado (a resposta do "concluir" se perdeu) ou o MinIO já descartou:
        # sobra, no máximo, o vídeo inteiro.
    if Training.objects.filter(video_file=dados['nome']).exists() or _tamanho(cliente, storage, chave) is None:
        return False
    cliente.delete_object(Bucket=storage.bucket_name, Key=chave)
    return True
