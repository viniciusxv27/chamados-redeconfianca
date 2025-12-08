from django import template
import re
from urllib.parse import urlparse, parse_qs

register = template.Library()


@register.filter
def youtube_embed_url(url):
    """
    Extrai o ID do vídeo do YouTube e retorna a URL de embed
    Suporta vários formatos de URL do YouTube
    """
    if not url:
        return ''
    
    # Padrões de URL do YouTube
    patterns = [
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/embed\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtu\.be\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/v\/([a-zA-Z0-9_-]{11})',
    ]
    
    video_id = None
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            break
    
    if video_id:
        return f'https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1&playsinline=1'
    
    return url


@register.filter
def vimeo_embed_url(url):
    """
    Extrai o ID do vídeo do Vimeo e retorna a URL de embed
    """
    if not url:
        return ''
    
    # Padrão de URL do Vimeo
    pattern = r'(?:https?:\/\/)?(?:www\.)?vimeo\.com\/(?:channels\/(?:\w+\/)?|groups\/([^\/]*)\/videos\/|album\/(\d+)\/video\/|)(\d+)(?:$|\/|\?)'
    
    match = re.search(pattern, url)
    if match:
        video_id = match.group(3)
        return f'https://player.vimeo.com/video/{video_id}?title=0&byline=0&portrait=0'
    
    return url


@register.filter
def is_youtube(url):
    """Verifica se a URL é do YouTube"""
    if not url:
        return False
    return 'youtube.com' in url or 'youtu.be' in url


@register.filter
def is_vimeo(url):
    """Verifica se a URL é do Vimeo"""
    if not url:
        return False
    return 'vimeo.com' in url


@register.filter
def video_type(filename):
    """Retorna o tipo MIME do vídeo baseado na extensão"""
    if not filename:
        return 'video/mp4'
    
    ext = filename.lower().split('.')[-1]
    
    types = {
        'mp4': 'video/mp4',
        'webm': 'video/webm',
        'ogg': 'video/ogg',
        'ogv': 'video/ogg',
        'avi': 'video/x-msvideo',
        'mov': 'video/quicktime',
        'wmv': 'video/x-ms-wmv',
        'flv': 'video/x-flv',
        'mkv': 'video/x-matroska',
    }
    
    return types.get(ext, 'video/mp4')
