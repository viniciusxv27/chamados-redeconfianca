"""Trava o portal de quem não concluiu uma trilha obrigatória no prazo.

Mesmo desenho do bloqueio de curso, com duas diferenças que vêm do pedido:

- **só gestor liga**: a trilha nasce com `blocks_portal=False` e apenas
  ADMIN/SUPERADMIN podem marcar. Supervisor cria trilha obrigatória, mas não
  tranca o portal de ninguém;
- **falha para o lado seguro**: erro nenhum tranca alguém do lado de fora.

O custo foi medido: a consulta só acontece quando existe alguma trilha com
bloqueio ligado, e o resultado fica 60 s em cache por pessoa — em toda
requisição logada, o caminho normal é um `get` no cache.
"""
import logging

from django.core.cache import caches
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

LIBERADOS = (
    '/logout', '/login', '/static/', '/media/', '/admin/',
    '/trilhas/', '/sw.js', '/OneSignalSDKWorker.js',
)
SEGUNDOS = 60


def pode_travar_portal(user):
    """Quem pode ligar o bloqueio numa trilha."""
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') in ('ADMIN', 'SUPERADMIN')))


def trilha_que_trava(user):
    """A trilha obrigatória, no prazo e não concluída que tranca esta pessoa.

    Devolve None quando não há nenhuma — que é o caso de quase todo mundo em
    quase toda requisição.
    """
    from .models import KnowledgeTrail, TrailProgress

    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN':
        return None

    hoje = timezone.localdate()
    pendentes = (KnowledgeTrail.objects
                 .filter(is_active=True, blocks_portal=True, mandatory_users=user)
                 .filter(mandatory_end_date__isnull=False, mandatory_end_date__lt=hoje))
    for trilha in pendentes:
        if trilha.mandatory_start_date and trilha.mandatory_start_date > hoje:
            continue
        concluida = TrailProgress.objects.filter(
            user=user, trail=trilha, status='completed').exists()
        if concluida and trilha.require_signature:
            # Concluir sem assinar não libera: a assinatura é o fecho pedido.
            concluida = trilha.signatures.filter(user=user).exists()
        if not concluida:
            return trilha
    return None


class BloqueioTrilhaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        if usuario is None or not usuario.is_authenticated:
            return self.get_response(request)
        if any(request.path.startswith(p) for p in LIBERADOS):
            return self.get_response(request)

        cache = caches['local']
        chave = f'trilhas:trava:{usuario.id}'
        travado = cache.get(chave)
        if travado is None:
            try:
                trilha = trilha_que_trava(usuario)
                travado = trilha.id if trilha else 0
            except Exception as exc:                      # nunca derruba a navegação
                logger.warning('Bloqueio de trilha ignorado por erro: %s', exc)
                travado = 0
            cache.set(chave, travado, SEGUNDOS)

        if travado:
            return redirect(reverse('knowledge_trails:trail_blocked', args=[travado]))
        return self.get_response(request)


def limpar_cache(user_id):
    """Chamado ao concluir/assinar, para a trava sair na hora."""
    caches['local'].delete(f'trilhas:trava:{user_id}')
