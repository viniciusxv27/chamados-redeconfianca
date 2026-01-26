import re
import html
from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def sanitize_html(value):
    """
    Sanitiza o conteúdo HTML removendo tags potencialmente perigosas,
    mas preservando a formatação básica (negrito, itálico, quebras de linha).
    
    Tags permitidas: b, strong, i, em, u, br, p, div
    Remove todas as outras tags e escapa o conteúdo.
    """
    if not value:
        return ''
    
    # Converter para string se necessário
    value = str(value)
    
    # Lista de tags permitidas (formatação básica)
    allowed_tags = ['b', 'strong', 'i', 'em', 'u', 'br', 'p', 'div']
    
    # Primeiro, vamos identificar e proteger as tags permitidas
    # Substitui tags permitidas por placeholders
    protected = {}
    placeholder_counter = 0
    
    for tag in allowed_tags:
        # Tags de abertura (com ou sem atributos)
        pattern_open = rf'<{tag}(\s[^>]*)?>'
        for match in re.finditer(pattern_open, value, re.IGNORECASE):
            placeholder = f'__ALLOWED_TAG_{placeholder_counter}__'
            protected[placeholder] = f'<{tag}>'  # Remove atributos por segurança
            value = value.replace(match.group(0), placeholder, 1)
            placeholder_counter += 1
        
        # Tags de fechamento
        pattern_close = rf'</{tag}>'
        for match in re.finditer(pattern_close, value, re.IGNORECASE):
            placeholder = f'__ALLOWED_TAG_{placeholder_counter}__'
            protected[placeholder] = f'</{tag}>'
            value = value.replace(match.group(0), placeholder, 1)
            placeholder_counter += 1
        
        # Tags auto-fechantes (como <br/> ou <br />)
        if tag == 'br':
            pattern_self = rf'<{tag}\s*/?>'
            for match in re.finditer(pattern_self, value, re.IGNORECASE):
                placeholder = f'__ALLOWED_TAG_{placeholder_counter}__'
                protected[placeholder] = '<br>'
                value = value.replace(match.group(0), placeholder, 1)
                placeholder_counter += 1
    
    # Remove todas as outras tags HTML (escapando o conteúdo)
    # Primeiro, escapa caracteres especiais que não são parte de tags
    value = re.sub(r'<[^>]+>', '', value)
    
    # Escapa qualquer < ou > restante
    value = value.replace('<', '&lt;').replace('>', '&gt;')
    
    # Restaura as tags permitidas
    for placeholder, tag in protected.items():
        value = value.replace(placeholder, tag)
    
    return mark_safe(value)


@register.filter
def strip_html(value):
    """
    Remove completamente todas as tags HTML, deixando apenas o texto.
    Também trata entidades HTML escapadas (&lt;, &gt;, &nbsp;, etc).
    Útil para casos onde nenhuma formatação HTML é desejada.
    """
    if not value:
        return ''
    
    value = str(value)
    
    # Primeiro, decodifica entidades HTML escapadas (&lt; → <, &gt; → >, &nbsp; → espaço)
    value = html.unescape(value)
    
    # Substitui <br> e </p> por quebras de linha antes de remover as tags
    value = re.sub(r'<br\s*/?>', '\n', value, flags=re.IGNORECASE)
    value = re.sub(r'</p>', '\n', value, flags=re.IGNORECASE)
    value = re.sub(r'</div>', '\n', value, flags=re.IGNORECASE)
    
    # Remove todas as tags HTML
    value = re.sub(r'<[^>]+>', '', value)
    
    # Remove múltiplas quebras de linha consecutivas (mais de 2)
    value = re.sub(r'\n{3,}', '\n\n', value)
    
    # Remove espaços em branco excessivos em cada linha
    lines = value.split('\n')
    lines = [re.sub(r' {2,}', ' ', line).strip() for line in lines]
    value = '\n'.join(lines)
    
    # Remove linhas completamente vazias no início e fim
    value = value.strip()
    
    # Escapa caracteres especiais HTML para segurança
    value = escape(value)
    
    # Converte quebras de linha para <br>
    value = value.replace('\n', '<br>')
    
    return mark_safe(value)
