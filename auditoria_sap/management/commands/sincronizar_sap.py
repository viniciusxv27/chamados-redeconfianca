"""Atualiza o espelho da auditoria SAP pela linha de comando."""
from django.core.management.base import BaseCommand

from auditoria_sap.espelho import sincronizar
from auditoria_sap.mysql import SapIndisponivel


class Command(BaseCommand):
    help = 'Lê a vw_auditoria_visao_geral do MySQL do SAP e atualiza o espelho local.'

    def handle(self, *args, **opcoes):
        try:
            resumo = sincronizar()
        except SapIndisponivel as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return
        self.stdout.write(self.style.SUCCESS(
            f"{resumo['total']} linhas · {resumo['novas']} novas · "
            f"{resumo['atualizadas']} atualizadas · {resumo['sumiram']} saíram "
            f"({resumo['segundos']} s)"))
