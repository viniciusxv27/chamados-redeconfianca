from django import template
from django.utils.safestring import mark_safe
import re

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Obtém um item do dicionário usando uma chave"""
    if dictionary and hasattr(dictionary, 'get'):
        return dictionary.get(key, '')
    return ''


@register.filter
def markdown_simple(text):
    """Converte markdown simples para HTML"""
    if not text:
        return ''
    
    # Converter quebras de linha duplas em parágrafos
    paragraphs = text.split('\n\n')
    html_parts = []
    
    for para in paragraphs:
        if not para.strip():
            continue
            
        # Converter listas
        if para.strip().startswith('-') or para.strip().startswith('•'):
            lines = para.split('\n')
            items = []
            for line in lines:
                line = line.strip()
                if line.startswith('-') or line.startswith('•'):
                    # Remover o marcador e processar formatação inline
                    item = line[1:].strip()
                    item = apply_inline_formatting(item)
                    items.append(f'<li>{item}</li>')
            if items:
                html_parts.append(f"<ul class='list-disc list-inside ml-4 space-y-1'>{''.join(items)}</ul>")
        else:
            # Processar formatação inline no parágrafo
            para = apply_inline_formatting(para)
            html_parts.append(f'<p class="mb-2">{para}</p>')
    
    return mark_safe(''.join(html_parts))


def apply_inline_formatting(text):
    """Aplica formatação inline (negrito, itálico)"""
    # Negrito: **texto** ou __texto__
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
    
    # Itálico: *texto* ou _texto_
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'_(.+?)_', r'<em>\1</em>', text)
    
    # Quebras de linha simples
    text = text.replace('\n', '<br>')
    
    return text
