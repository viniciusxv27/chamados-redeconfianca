from django import template
import re
from urllib.parse import urlparse, parse_qs

register = template.Library()


@register.filter
def youtube_embed_url(url):
    """
    Extrai o ID do vídeo do YouTube e retorna a URL de embed
    Suporta vários formatos de URL do YouTube incluindo shorts, live, etc.
    """
    if not url:
        return ''
    
    url = str(url).strip()
    
    # Se já é uma URL de embed válida, apenas garantir parâmetros corretos
    if 'youtube.com/embed/' in url:
        # Extrair apenas o ID do vídeo
        embed_match = re.search(r'youtube\.com\/embed\/([a-zA-Z0-9_-]{11})', url)
        if embed_match:
            video_id = embed_match.group(1)
            return f'https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1&playsinline=1&enablejsapi=1&origin={{}}'
        return url
    
    # Tentar extrair usando parse_qs para URLs com parâmetros
    try:
        parsed = urlparse(url)
        if parsed.hostname in ['www.youtube.com', 'youtube.com', 'm.youtube.com']:
            # URL padrão com ?v=
            query_params = parse_qs(parsed.query)
            if 'v' in query_params:
                video_id = query_params['v'][0][:11]  # Garantir apenas 11 caracteres
                return f'https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1&playsinline=1&enablejsapi=1'
            
            # Shorts
            shorts_match = re.search(r'/shorts/([a-zA-Z0-9_-]{11})', parsed.path)
            if shorts_match:
                video_id = shorts_match.group(1)
                return f'https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1&playsinline=1&enablejsapi=1'
            
            # Live
            live_match = re.search(r'/live/([a-zA-Z0-9_-]{11})', parsed.path)
            if live_match:
                video_id = live_match.group(1)
                return f'https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1&playsinline=1&enablejsapi=1'
                
        elif parsed.hostname in ['youtu.be']:
            # URL encurtada youtu.be/VIDEO_ID
            video_id = parsed.path.strip('/')[:11]
            if video_id and len(video_id) == 11:
                return f'https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1&playsinline=1&enablejsapi=1'
    except Exception:
        pass
    
    # Fallback: padrões regex para formatos variados
    patterns = [
        r'(?:https?:\/\/)?(?:www\.)?(?:m\.)?youtube\.com\/watch\?.*v=([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/embed\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtu\.be\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/v\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/shorts\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/live\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube-nocookie\.com\/embed\/([a-zA-Z0-9_-]{11})',
    ]
    
    video_id = None
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            break
    
    if video_id and len(video_id) == 11:
        return f'https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1&playsinline=1&enablejsapi=1'
    
    # Se nada funcionar, retornar string vazia para evitar erros no player
    return ''


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
    url_lower = str(url).lower()
    return any(domain in url_lower for domain in [
        'youtube.com', 
        'youtu.be', 
        'youtube-nocookie.com',
        'm.youtube.com'
    ])


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
    
    # Converter para string caso seja um FieldFile
    filename_str = str(filename)
    
    # Extrair extensão corretamente
    if '.' in filename_str:
        ext = filename_str.lower().rsplit('.', 1)[-1]
    else:
        return 'video/mp4'
    
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
        'm4v': 'video/mp4',
        '3gp': 'video/3gpp',
    }
    
    return types.get(ext, 'video/mp4')
