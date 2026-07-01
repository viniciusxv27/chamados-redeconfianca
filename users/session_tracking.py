"""Rastreamento de sessões de usuário: geolocalização por IP, captura no login
e atualização de atividade a cada requisição.

O objetivo é manter a tabela ``UserSession`` sincronizada com as sessões reais
do Django, guardando IP, localização aproximada e informações do dispositivo.
"""
import ipaddress

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils.deprecation import MiddlewareMixin

# Cache simples em memória de processo para evitar repetir chamadas externas
# de geolocalização para o mesmo IP.
_LOCATION_CACHE = {}


def _get_client_ip(request):
    """Extrai o IP do cliente respeitando proxies (X-Forwarded-For)."""
    ip = getattr(request, 'user_ip', None)
    if ip:
        return ip
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _is_private_ip(ip):
    try:
        return ipaddress.ip_address(ip).is_private
    except (ValueError, TypeError):
        return False


def get_location_from_ip(ip):
    """Retorna uma string de localização aproximada a partir do IP.

    Best-effort: usa o serviço gratuito ip-api.com com timeout curto. Nunca
    levanta exceção — em caso de falha retorna string vazia. IPs privados/locais
    são identificados como "Rede Interna".
    """
    if not ip:
        return ''

    if ip in _LOCATION_CACHE:
        return _LOCATION_CACHE[ip]

    if _is_private_ip(ip) or ip in ('127.0.0.1', '::1', 'localhost'):
        _LOCATION_CACHE[ip] = 'Rede Interna'
        return 'Rede Interna'

    location = ''
    try:
        import requests
        resp = requests.get(
            f'http://ip-api.com/json/{ip}',
            params={'fields': 'status,country,regionName,city', 'lang': 'pt-BR'},
            timeout=2.5,
        )
        data = resp.json()
        if data.get('status') == 'success':
            parts = [data.get('city'), data.get('regionName'), data.get('country')]
            location = ' - '.join([p for p in parts if p])
    except Exception:
        location = ''

    # Cacheia o resultado (mesmo vazio) para não repetir a chamada externa a cada
    # requisição do mesmo IP. O cache é por processo e é limpo em cada redeploy,
    # permitindo nova tentativa após reinício.
    _LOCATION_CACHE[ip] = location
    return location


def _sync_session(request, user):
    """Cria/atualiza o registro UserSession para a sessão atual.

    Caminho comum (sessão já registrada): um único UPDATE, sem SELECT extra.
    A geolocalização é resolvida apenas na criação do registro.
    """
    from django.utils import timezone
    from .models import UserSession

    session_key = request.session.session_key
    if not session_key:
        # Garante que a sessão tenha uma chave persistida.
        request.session.save()
        session_key = request.session.session_key
    if not session_key:
        return

    ip = _get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    now = timezone.now()

    # Tenta atualizar primeiro (caso comum). update() não dispara auto_now,
    # por isso last_activity é passado explicitamente.
    updated = UserSession.objects.filter(session_key=session_key).update(
        user=user, ip_address=ip, user_agent=user_agent, last_activity=now,
    )
    if updated:
        return

    # Registro ainda não existe: cria e resolve a localização uma única vez.
    from django.db import IntegrityError
    location = get_location_from_ip(ip) if ip else ''
    try:
        UserSession.objects.create(
            session_key=session_key,
            user=user,
            ip_address=ip,
            user_agent=user_agent,
            location=location,
        )
    except IntegrityError:
        # Corrida com outra requisição da mesma sessão nova: apenas atualiza.
        UserSession.objects.filter(session_key=session_key).update(
            user=user, ip_address=ip, user_agent=user_agent, last_activity=now,
        )


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    """Registra a sessão assim que o usuário faz login."""
    if request is None:
        return
    try:
        _sync_session(request, user)
    except Exception:
        # Nunca quebra o fluxo de login por causa do rastreamento.
        pass


class ActiveSessionMiddleware(MiddlewareMixin):
    """Mantém ``last_activity`` atualizado e garante um registro de sessão para
    usuários já autenticados (inclusive sessões criadas antes deste recurso)."""

    def process_request(self, request):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return None
        try:
            _sync_session(request, user)
        except Exception:
            pass
        return None
