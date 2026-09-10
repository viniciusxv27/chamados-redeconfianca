"""Armazenamento PRIVADO para a credencial do Google Drive.

O bucket de mídia do portal é ``public-read``; uma chave de service account é um
segredo e NUNCA pode ficar pública. Este storage força ACL privada — o arquivo
só é lido pelo servidor (via ``.open()``, autenticado nas chaves do S3), nunca
por um link público. Guardado sob o prefixo ``drive/credenciais/``.
"""
from storages.backends.s3boto3 import S3Boto3Storage


class DriveCredentialStorage(S3Boto3Storage):
    default_acl = 'private'
    querystring_auth = True
    file_overwrite = True


class DrivePreviewStorage(S3Boto3Storage):
    """Prévias em PDF dos arquivos do Drive (Word/Excel/PowerPoint convertidos).

    Privadas pelo mesmo motivo da credencial: a prévia É o conteúdo do arquivo.
    No bucket público de mídia, qualquer um com o link leria um documento que o
    portal só mostra depois de checar a permissão do setor. Como a ACL por
    objeto não é garantia em todo S3 compatível, o conteúdo ainda vai cifrado
    (drive/visualizacao.py).
    """
    default_acl = 'private'
    querystring_auth = True
    file_overwrite = True
