"""Trava o portal de quem passou do prazo do curso.

Copia o desenho do bloqueio de ponto: falha para o lado seguro (erro nenhum
tranca alguém do lado de fora), deixa passar o SUPERADMIN e mantém abertos os
caminhos de que a própria tela de bloqueio precisa — inclusive o envio do
comprovante, que é a saída da tela.
"""
import logging

from django.shortcuts import redirect
from django.urls import reverse

logger = logging.getLogger(__name__)

LIBERADOS = (
    '/logout', '/login', '/static/', '/media/', '/admin/',
    '/cursos/', '/sw.js', '/OneSignalSDKWorker.js',
)


class BloqueioCursoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        if usuario is None or not usuario.is_authenticated:
            return self.get_response(request)

        if any(request.path.startswith(p) for p in LIBERADOS):
            return self.get_response(request)

        if usuario.is_superuser or getattr(usuario, 'hierarchy', '') == 'SUPERADMIN':
            return self.get_response(request)

        try:
            from .models import ConfiguracaoCursos
            from .permissions import vencidos_sem_comprovante

            cfg = ConfiguracaoCursos.get()
            if not cfg.bloquear_navegacao:
                return self.get_response(request)
            if cfg.e_gestor(usuario):
                return self.get_response(request)

            # Ordem de prioridade: PRIMEIRO o comunicado, DEPOIS o curso.
            # Enquanto houver um popup bloqueante pendente (ex.: comunicado
            # obrigatório a ler), o bloqueio de curso cede a vez — senão as duas
            # travas se mordem: o popup do comunicado cobre a tela do curso e,
            # ao clicar para abrir o comunicado, este middleware jogava a pessoa
            # de volta para /cursos/bloqueado/, sem nunca deixar concluir nenhum.
            from portal_popups.context_processors import tem_popup_bloqueante_pendente
            if tem_popup_bloqueante_pendente(usuario):
                return self.get_response(request)

            if vencidos_sem_comprovante(usuario, cfg):
                return redirect(reverse('cursos:bloqueado'))
        except Exception as exc:                     # nunca derruba a navegação
            logger.warning('Bloqueio de curso ignorado por erro: %s', exc)

        return self.get_response(request)
