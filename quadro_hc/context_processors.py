from .servicos import pode_acessar


def quadro_hc_menu(request):
    return {'quadro_hc_liberado': pode_acessar(getattr(request, 'user', None))}
