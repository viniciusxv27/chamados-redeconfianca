"""Módulos do portal no app (Impulso, Ponto, Contracheque, Documentos…).

Cada módulo mora no seu arquivo (``api_mobile/modulos/<chave>.py``) e declara:

- ``MODULO``: rótulo, ícone (Ionicons), cor e ``pode_entrar(user)`` — a MESMA
  regra que o portal usa para deixar a pessoa entrar no módulo (gate do menu /
  da view web). Nada de regra nova aqui: chama a função do próprio app.
- ``urlpatterns``: os endpoints, todos com ``permissao(<chave>)``.

O app pergunta ``GET /api/mobile/v1/modulos/`` e mostra só o que a pessoa pode
abrir; e cada endpoint confere de novo (o app não é a trava, o servidor é).
"""
import logging
from importlib import import_module

from rest_framework.exceptions import PermissionDenied

from ..permissoes import SemBloqueio

logger = logging.getLogger(__name__)

# Ordem em que aparecem no app.
CHAVES = ['impulso', 'ponto', 'agenda', 'contracheque', 'documentos', 'comissao', 'simulador', 'vendas',
          'checklists', 'quadro_hc', 'cameras', 'usuarios']


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

    class ModuloLiberado(SemBloqueio):
        def has_permission(self, request, view):
            if not super().has_permission(request, view):
                return False
            if not pode_entrar(request.user, chave):
                raise PermissionDenied(f'Você não tem acesso a {carregar(chave)["rotulo"]}.')
            return True

    ModuloLiberado.__name__ = f'ModuloLiberado_{chave}'
    return ModuloLiberado
