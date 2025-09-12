"""
Custom storage backends for MinIO S3
"""
from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage


class MediaStorage(S3Boto3Storage):
    """Storage for user uploaded media files"""
    location = 'media'
    default_acl = 'public-read'
    file_overwrite = False


class TrainingStorage(S3Boto3Storage):
    """Storage for training videos and files"""
    location = 'trainings'
    default_acl = 'public-read'
    file_overwrite = False


class StaticStorage(S3Boto3Storage):
    """Storage for static files (if needed)"""
    location = 'static'
    default_acl = 'public-read'
    file_overwrite = True


def get_media_storage():
    """Return appropriate media storage backend"""
    if getattr(settings, 'USE_S3', False):
        return MediaStorage()
    return None


def get_training_storage():
    """Return appropriate training storage backend"""
    if getattr(settings, 'USE_S3', False):
        return TrainingStorage()
    return None