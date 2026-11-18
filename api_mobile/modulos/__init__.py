"""Módulos do portal no app (Impulso, Ponto, Contracheque, Documentos…).

Cada módulo mora no seu arquivo (``api_mobile/modulos/<chave>.py``) e declara:

- ``MODULO``: rótulo, ícone (Ionicons), cor, ``libera_no_bloqueio`` (tipos de bloqueio do
  portal em que o módulo continua aberto, como os LIBERADOS dos middlewares) e
  ``pode_entrar(user)`` — a MESMA
  regra que o portal usa para deixar a pessoa entrar no módulo (gate do menu /
  da view web). Nada de regra nova aqui: chama a função do próprio app.
- ``urlpatterns``: os endpoints, todos com ``permissao(<chave>)``.

O app pergunta ``GET /api/mobile/v1/modulos/`` e mostra só o que a pessoa pode
abrir; e cada endpoint confere de novo (o app não é a trava, o servidor é).
"""
import logging
from importlib import import_module

from rest_framework.exceptions import PermissionDenied

from rest_framework.permissions import BasePermission

from ..permissoes import PortalBloqueado, bloqueio_do_usuario

logger = logging.getLogger(__name__)

# Ordem em que aparecem no app.
CHAVES = ['impulso', 'ponto', 'agenda', 'cursos', 'contracheque', 'documentos', 'comissao', 'simulador', 'vendas',
          'power_bi', 'power_bi_metas', 'mercadinho', 'facilitador', 'checklists', 'quadro_hc', 'cameras', 'usuarios']


def carregar(chave):
    return import_module(f'{__name__}.{chave}').MODULO


def pode_entrar(user, chave):
    """A regra de entrada do módulo; erro conta como 'não' (nunca abre porta por exceção)."""
    try:
        return bool(carregar(chave)['pode_entrar'](user))
    except Exception as exc:  # noqa: BLE001
        logger.warning('Gate do módulo %s falhou para %s: %s', chave, getattr(user, 'pk', None), exc)
        return False


def modulos_do_usuario(user):
    saida = []
    for chave in CHAVES:
        if not pode_entrar(user, chave):
            continue
        m = carregar(chave)
        saida.append({'chave': chave, 'rotulo': m['rotulo'], 'descricao': m.get('descricao', ''),
                      'icone': m['icone'], 'cor': m['cor'], 'rota': f'/modulo/{chave}'})
    return saida


def permissao(chave):
    """Classe de permissão do DRF: logado, sem bloqueio do portal e com acesso ao módulo."""

    class ModuloLiberado(BasePermission):
        def has_permission(self, request, view):
            user = request.user
            if not (user and user.is_authenticated):
                return False
            # Os bloqueios do portal valem aqui — menos o que o próprio bloqueio deixa aberto na
            # web para a pessoa se resolver (os LIBERADOS dos middlewares: /cursos/ no de curso).
            bloqueio = bloqueio_do_usuario(user)
            if bloqueio and bloqueio['tipo'] not in carregar(chave).get('libera_no_bloqueio', ()):
                raise PortalBloqueado(bloqueio)
            if not pode_entrar(user, chave):
                raise PermissionDenied(f'Você não tem acesso a {carregar(chave)["rotulo"]}.')
            return True

    ModuloLiberado.__name__ = f'ModuloLiberado_{chave}'
    return ModuloLiberado
