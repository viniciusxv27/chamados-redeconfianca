"""Auditoria do Drive (RF34–36). Nunca derruba a ação por causa do registro."""
import logging

from .models import DriveAuditLog

logger = logging.getLogger(__name__)


def ip_do(request):
    if not request:
        return None
    fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return (fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR'))


def registrar(user, acao, request=None, file_id='', file_name='', sector=None, folder_id='', detalhe=''):
    try:
        DriveAuditLog.objects.create(
            user=user if getattr(user, 'is_authenticated', False) else None,
            acao=acao,
            file_id=(file_id or '')[:100],
            file_name=(file_name or '')[:255],
            sector=sector,
            folder_id=(folder_id or '')[:100],
            detalhe=(detalhe or '')[:255],
            ip=ip_do(request),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning('Log do Drive não gravado: %s', exc)
