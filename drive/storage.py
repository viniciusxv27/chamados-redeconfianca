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
