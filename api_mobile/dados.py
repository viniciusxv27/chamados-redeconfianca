"""Como cada coisa do portal vira JSON para o app.

Funções simples (sem serializer do DRF): o app só lê, e cada tela pede um
formato enxuto. Datas em ISO 8601 com fuso; URLs de arquivo sempre absolutas.
"""
import html
import re

from django.utils import timezone
from django.utils.html import strip_tags

# Situação do chamado para o app: o que ainda pede ação × o que terminou.
STATUS_EM_ABERTO = ('ABERTO', 'EM_ANDAMENTO', 'REABERTO', 'RESOLVIDO', 'AGUARDANDO_APROVACAO')
STATUS_CONCLUIDOS = ('FECHADO', 'ENCERRADO_SEM_SOLUCAO', 'REJEITADO')


def iso(valor):
    if not valor:
        return None
    if hasattr(valor, 'tzinfo') and timezone.is_naive(valor):
        valor = timezone.make_aware(valor)
    return valor.isoformat()


def absoluta(request, caminho):
    """URL absoluta para o app. Atrás do proxy HTTPS de produção o Django enxerga
    http (não há SECURE_PROXY_SSL_HEADER); o cabeçalho do proxy decide o esquema."""
    url = request.build_absolute_uri(caminho)
    if url.startswith('http://') and request.META.get('HTTP_X_FORWARDED_PROTO', '').split(',')[0].strip() == 'https':
        url = 'https://' + url[len('http://'):]
    return url


def url_absoluta(request, campo):
    """URL pública de um FileField/ImageField (MinIO já vem absoluta; disco vem com /media/)."""
    try:
        if not campo:
            return None
        url = campo.url
    except Exception:                          # arquivo sumido do storage não derruba a tela
        return None
    if url.startswith('/'):
        return absoluta(request, url)
    return url


def texto_do_html(conteudo, limite=None):
    """HTML do comunicado → texto com parágrafos (o app não renderiza HTML)."""
    if not conteudo:
        return ''
    t = re.sub(r'(?i)<br\s*/?>', '\n', conteudo)
    t = re.sub(r'(?i)</(p|div|li|h[1-6]|tr)>', '\n', t)
    t = re.sub(r'(?i)<li[^>]*>', '• ', t)
    t = html.unescape(strip_tags(t))
    t = re.sub(r'[ \t ]+', ' ', t)
    t = re.sub(r'\n\s*\n\s*\n+', '\n\n', t).strip()
    if limite and len(t) > limite:
        t = t[:limite].rsplit(' ', 1)[0].rstrip(' .,;:') + '…'
    return t


def pessoa(request, user, completo=False):
    if user is None:
        return None
    setor = user.sector
    dados = {
        'id': user.pk,
        'nome': user.get_full_name() or user.email,
        'primeiro_nome': user.first_name or (user.get_full_name() or user.email).split(' ')[0],
        'foto': url_absoluta(request, getattr(user, 'profile_picture', None)),
        'setor': setor.name if setor else None,
    }
    if completo:
        dados.update({
            'email': user.email,
            'cargo': user.job_title or None,
            'telefone': user.phone or None,
            'hierarquia': user.hierarchy,
            'hierarquia_nome': user.get_hierarchy_display(),
            'superadmin': bool(user.is_superuser or user.hierarchy == 'SUPERADMIN'),
            'tema': getattr(user, 'theme', None) or 'light',
            'setores': sorted({s.name for s in user.sectors.all()} | ({setor.name} if setor else set())),
        })
    return dados


def chamado_resumo(request, t):
    return {
        'id': t.pk,
        'titulo': t.title,
        'status': t.status,
        'status_nome': t.get_status_display(),
        'prioridade': t.priority,
        'prioridade_nome': t.get_priority_display(),
        'setor': t.sector.name if t.sector_id else None,
        'categoria': t.category.name if t.category_id else None,
        'solicitante': pessoa(request, t.created_by),
        'responsavel': pessoa(request, t.assigned_to),
        'criado_em': iso(t.created_at),
        'atualizado_em': iso(t.updated_at),
        'prazo': iso(t.due_date),
        'atrasado': bool(t.due_date and t.status in STATUS_EM_ABERTO and t.due_date < timezone.now()),
        'em_aberto': t.status in STATUS_EM_ABERTO,
    }


def comunicado_resumo(request, c, lidos=None, curtidos=None):
    imagens = [url_absoluta(request, i.image) for i in c.images.all()]
    if c.image:
        imagens.insert(0, url_absoluta(request, c.image))
    return {
        'id': c.pk,
        'titulo': c.title,
        'cor_titulo': c.title_color or None,
        'resumo': texto_do_html(c.message, 180),
        'imagem': next((i for i in imagens if i), None),
        'fixado': c.is_pinned,
        'obrigatorio': c.obrigatorio,
        'autor': pessoa(request, c.sender),
        'criado_em': iso(c.created_at),
        'curtidas': len(c.liked_by.all()),
        'curti': bool(curtidos and c.pk in curtidos),
        'situacao': (lidos or {}).get(c.pk, 'NAO_VISUALIZADO'),
    }
