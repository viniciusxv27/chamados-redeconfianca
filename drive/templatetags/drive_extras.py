from django import template

register = template.Library()

# (trecho no mimeType, ícone FontAwesome, cor)
_ICONS = [
    ('application/vnd.google-apps.folder', 'fa-folder', 'text-amber-400'),
    ('pdf', 'fa-file-pdf', 'text-red-500'),
    ('image/', 'fa-file-image', 'text-emerald-500'),
    ('spreadsheet', 'fa-file-excel', 'text-green-600'),
    ('sheet', 'fa-file-excel', 'text-green-600'),
    ('presentation', 'fa-file-powerpoint', 'text-orange-500'),
    ('document', 'fa-file-word', 'text-blue-600'),
    ('word', 'fa-file-word', 'text-blue-600'),
    ('zip', 'fa-file-zipper', 'text-yellow-600'),
    ('csv', 'fa-file-csv', 'text-teal-600'),
    ('text/', 'fa-file-lines', 'text-gray-500'),
    ('video/', 'fa-file-video', 'text-purple-500'),
    ('audio/', 'fa-file-audio', 'text-pink-500'),
]


@register.filter
def file_icon(mime):
    m = (mime or '').lower()
    for chave, icon, cor in _ICONS:
        if chave in m:
            return f'{icon} {cor}'
    return 'fa-file text-gray-400'


@register.simple_tag
def meu_drive_ligado(user):
    """A aba "Meu Drive" aparece? Só para o SUPERADMIN com a conta conectada."""
    try:
        from drive import permissions as perms
        from drive.models import DriveConfig
        if not perms.is_superadmin(user):
            return False
        cfg = DriveConfig.objects.filter(pk=1).first()
        return bool(cfg and cfg.usa_conta_propria and cfg.oauth_refresh_token)
    except Exception:  # noqa: BLE001 — a aba nunca derruba a tela
        return False
