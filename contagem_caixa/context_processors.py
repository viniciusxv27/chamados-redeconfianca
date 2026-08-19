"""Gate do item de menu da Contagem de Caixa."""
from .permissions import pode_ver_caixa


def caixa_menu(request):
    return {'caixa_liberado': pode_ver_caixa(getattr(request, 'user', None))}
