"""Bloqueio de navegação para quem está de férias.

Quem está em período de férias aprovado não navega pelo portal: cai numa tela
explicando até quando. O bloqueio é deliberadamente **frouxo em caso de erro** —
se a API do Tangerino estiver fora, ninguém é trancado do lado de fora.
"""
import logging

from django.shortcuts import redirect
from django.urls import reverse

from .ferias import esta_de_ferias

logger = logging.getLogger(__name__)

# Caminhos que continuam abertos: sair da conta, a própria tela de férias, o
# painel de férias (a pessoa pode querer conferir as datas), arquivos estáticos
# e as APIs internas que a tela de bloqueio usa.
LIBERADOS = (
    '/logout', '/login', '/static/', '/media/', '/admin/',
    '/ferias/', '/sw.js', '/OneSignalSDKWorker.js',
)


class BloqueioFeriasMiddleware:
    """Redireciona colaboradores de férias para a tela de aviso."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        if usuario is None or not usuario.is_authenticated:
            return self.get_response(request)

        caminho = request.path
        if any(caminho.startswith(p) for p in LIBERADOS):
            return self.get_response(request)

        # Administradores continuam entrando: quem cuida do portal pode precisar
        # trabalhar mesmo estando de férias no sistema de ponto.
        if usuario.is_superuser or getattr(usuario, 'hierarchy', '') == 'SUPERADMIN':
            return self.get_response(request)

        try:
            # O bloqueio é opcional e vale apenas para quem tem o módulo
            # liberado — quem está fora do grupo nem sabe que ele existe.
            from .models import ConfiguracaoTangerino
            config = ConfiguracaoTangerino.get()
            if not (config.bloquear_navegacao_ferias and config.libera(usuario)):
                return self.get_response(request)

            if esta_de_ferias(usuario):
                return redirect(reverse('tangerino:em_ferias'))
        except Exception as exc:                     # nunca derruba a navegação
            logger.warning('Bloqueio de férias ignorado por erro: %s', exc)

        return self.get_response(request)
