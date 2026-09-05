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
