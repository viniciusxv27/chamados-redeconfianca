"""Bloqueio de navegação para colaboradores com pré-cadastro reprovado.

Enquanto o pré-cadastro estiver reprovado, o colaborador só acessa a tela de
ajuste (onde vê o motivo e reenvia os dados/documentos). Assim que ele reenvia,
o status volta para "concluído" e a navegação é liberada normalmente.
"""
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin


class PreRegistrationAdjustmentMiddleware(MiddlewareMixin):
    """Redireciona para a tela de ajuste quem tem pré-cadastro reprovado."""

    # Telas que continuam acessíveis para não prender o usuário fora do fluxo.
    ALLOWED_URL_NAMES = {
        'adjust_pre_registration',
        'login',
        'logout',
        'service_worker',
        'onesignal_worker',
    }

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return None

        # Usuários do django admin / sem o campo (ex.: AnonymousUser) passam direto.
        needs_adjustment = getattr(user, 'needs_pre_registration_adjustment', None)
        if not callable(needs_adjustment) or not needs_adjustment():
            return None

        match = request.resolver_match
        if match and match.url_name in self.ALLOWED_URL_NAMES:
            return None

        path = request.path
        for prefix in (settings.STATIC_URL, settings.MEDIA_URL):
            if prefix and path.startswith(prefix):
                return None
        if path.startswith('/admin/'):
            return None

        # Clientes JSON recebem erro em vez de um redirect em HTML.
        if path.startswith('/api/') or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse(
                {'error': 'Seu cadastro precisa de ajustes antes de continuar.'},
                status=403,
            )

        messages.warning(request, 'Seu cadastro precisa de ajustes antes de você continuar usando o portal.')
        return redirect('adjust_pre_registration')
