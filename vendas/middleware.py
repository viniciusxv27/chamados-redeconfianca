"""Acorda o espelho do Vivo GO de 5 em 5 minutos (ver ``vendas/vivogo.py``).

Mesmo arranjo do SAP: sai daqui em microssegundos; quem lê é uma thread, e só
um processo ganha a vez.
"""
from .vivogo import disparar_se_esta_na_hora


class EspelhoVivoGoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        if usuario is not None and usuario.is_authenticated:
            disparar_se_esta_na_hora()
        return self.get_response(request)
