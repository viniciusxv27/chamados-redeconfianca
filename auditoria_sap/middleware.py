"""Acorda a leitura automática do SAP (ver ``auditoria_sap/agendador.py``).

Sem cron em produção: a primeira visita de alguém logado depois do intervalo
dispara a leitura numa thread. Sai daqui em microssegundos — o agendador só
consulta o banco uma vez por minuto por processo.
"""
from .agendador import disparar_se_esta_na_hora


class LeituraAgendadaSapMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        if usuario is not None and usuario.is_authenticated:
            disparar_se_esta_na_hora()
        return self.get_response(request)
