"""Grava cada página aberta e cada envio de quem está logado (core.AcessoPagina) — 24/11/2026.

O que entra:
- **página**: GET de navegação (o navegador manda ``Sec-Fetch-Mode: navigate``; WebView antiga
  sem esse cabeçalho entra quando pede HTML e não é fetch/XHR). Download por link também é
  navegação, então entra;
- **envio**: todo POST/PUT/PATCH/DELETE (salvar, aprovar, excluir, enviar arquivo...).

O que não entra: estáticos e mídia, chamadas de fundo (fetch/polling — seriam milhares por
hora e não são "acessos"), GET da API do app (o app consulta sozinho) e o corpo do POST (pode
ter senha). Gravar nunca derruba a resposta: erro aqui só vai para o log.
"""
import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)

IGNORAR_PREFIXOS = ('/static/', '/media/', '/favicon', '/sw.js', '/OneSignalSDK', '/robots.txt', '/manifest',
                    '/__debug__/', '/apple-touch-icon')
METODOS_DE_ENVIO = {'POST', 'PUT', 'PATCH', 'DELETE'}


def _ip(request):
    ip = getattr(request, 'user_ip', None)
    if ip:
        return ip
    encaminhado = request.META.get('HTTP_X_FORWARDED_FOR')
    if encaminhado:
        return encaminhado.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def deve_registrar(request):
    caminho = request.path
    if caminho.startswith(IGNORAR_PREFIXOS):
        return False
    metodo = request.method.upper()
    if metodo in METODOS_DE_ENVIO:
        return True
    if metodo != 'GET' or caminho.startswith('/api/'):
        return False
    modo = request.META.get('HTTP_SEC_FETCH_MODE')
    if modo:
        return modo == 'navigate'
    if request.META.get('HTTP_X_REQUESTED_WITH'):
        return False
    return 'text/html' in (request.META.get('HTTP_ACCEPT') or '')


class RegistroDeAcessosMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inicio = time.monotonic()
        response = self.get_response(request)
        if not getattr(settings, 'REGISTRAR_ACESSOS', True):
            return response
        try:
            user = getattr(request, 'user', None)
            if user is not None and user.is_authenticated and deve_registrar(request):
                from .models import AcessoPagina
                rota = getattr(getattr(request, 'resolver_match', None), 'view_name', '') or ''
                AcessoPagina.objects.create(
                    user_id=user.pk,
                    metodo=request.method.upper()[:8],
                    caminho=request.path[:500],
                    consulta=(request.META.get('QUERY_STRING') or '')[:500],
                    rota=rota[:150],
                    status=getattr(response, 'status_code', None),
                    duracao_ms=int((time.monotonic() - inicio) * 1000),
                    ip=_ip(request) or None,
                    navegador=(request.META.get('HTTP_USER_AGENT') or '')[:300],
                )
        except Exception:  # noqa: BLE001 — o registro nunca pode quebrar a página
            logger.warning('Registro de acesso falhou em %s', request.path, exc_info=True)
        return response


def limpar_antigos():
    """Tarefa diária: apaga, em lotes curtos, os acessos mais velhos que o prazo de guarda."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import AcessoPagina
    dias = getattr(settings, 'ACESSOS_DIAS_GUARDADOS', 400)
    limite = timezone.now() - timedelta(days=dias)
    total = 0
    while True:
        ids = list(AcessoPagina.objects.filter(criado_em__lt=limite).values_list('id', flat=True)[:5000])
        if not ids:
            return total
        total += AcessoPagina.objects.filter(id__in=ids).delete()[0]
