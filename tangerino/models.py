from django.conf import settings
from django.db import models


class SincronizacaoTangerino(models.Model):
    """Registro de cada rodada de sincronização, para auditoria na tela admin."""

    class Tipo(models.TextChoices):
        VINCULO = 'VINCULO', 'Vínculo de funcionários'

    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.VINCULO)
    executada_em = models.DateTimeField(auto_now_add=True)
    executada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sincronizacoes_tangerino', verbose_name='Executada por')

    casados_cpf = models.PositiveIntegerField(default=0, verbose_name='Casados por CPF')
    casados_nome = models.PositiveIntegerField(default=0, verbose_name='Casados por nome')
    ja_vinculados = models.PositiveIntegerField(default=0, verbose_name='Já vinculados')
    sem_correspondencia = models.PositiveIntegerField(default=0, verbose_name='Sem correspondência')
    sucesso = models.BooleanField(default=True)
    detalhe = models.TextField(blank=True, verbose_name='Detalhe / erro')

    class Meta:
        verbose_name = 'Sincronização com o Tangerino'
        verbose_name_plural = 'Sincronizações com o Tangerino'
        ordering = ['-executada_em']

    def __str__(self):
        return f"{self.get_tipo_display()} em {self.executada_em:%d/%m/%Y %H:%M}"

    @property
    def total_vinculados(self):
        return self.casados_cpf + self.casados_nome + self.ja_vinculados


class RegistroPontoPortal(models.Model):
    """Trilha local de cada marcação batida PELO PORTAL.

    O registro oficial é o do Tangerino; isto aqui é só a prova de quem apertou
    o botão daqui, com IP e horário — útil quando alguém questiona a origem de
    uma marcação.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='marcacoes_portal', verbose_name='Usuário')
    employee_id = models.IntegerField(verbose_name='ID no Tangerino')
    momento = models.DateTimeField(verbose_name='Horário registrado')
    criado_em = models.DateTimeField(auto_now_add=True)
    atrasado = models.BooleanField(default=False, verbose_name='Marcação retroativa')
    justificativa = models.CharField(max_length=200, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    sucesso = models.BooleanField(default=True)
    retorno = models.TextField(blank=True, verbose_name='Resposta do Tangerino')

    class Meta:
        verbose_name = 'Ponto batido pelo portal'
        verbose_name_plural = 'Pontos batidos pelo portal'
        ordering = ['-criado_em']

    def __str__(self):
        return f"{self.usuario} em {self.momento:%d/%m/%Y %H:%M}"
