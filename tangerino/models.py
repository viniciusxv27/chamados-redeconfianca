from django.conf import settings
from django.db import models


# ─── Configuração / liga-desliga ─────────────────────────────────────────────

class ConfiguracaoTangerino(models.Model):
    """Chaves de liga-desliga do módulo, editáveis pela tela sem deploy.

    Registro único (id=1). Tudo nasce fechado: o módulo abre só para o grupo
    escolhido e o registro de ponto pelo portal começa desligado, servindo
    apenas como informação até alguém decidir o contrário.
    """

    ativo = models.BooleanField(
        default=True, verbose_name='Módulo Ponto e Férias ativo',
        help_text='Desmarque para esconder as telas de ponto e férias. '
                  'Superusuários continuam entrando, para poder religar.')
    restrito_ao_grupo = models.BooleanField(
        default=True, verbose_name='Liberar apenas para o grupo escolhido',
        help_text='Desmarque para liberar o módulo para todo o portal.')
    grupo = models.ForeignKey(
        'communications.CommunicationGroup', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name='Grupo liberado',
        help_text='Grupo de comunicação cujos membros enxergam o módulo.')

    permitir_bater_ponto = models.BooleanField(
        default=False, verbose_name='Permitir bater ponto pelo portal',
        help_text='Desligado, as telas mostram o ponto apenas como informação.')
    exigir_foto = models.BooleanField(
        default=True, verbose_name='Exigir foto ao bater ponto',
        help_text='O Tangerino recusa marcação pela web sem foto nesta empresa.')
    permitir_ponto_atrasado = models.BooleanField(
        default=False, verbose_name='Permitir marcação retroativa pelo portal')

    mostrar_widget_home = models.BooleanField(
        default=True, verbose_name='Mostrar o cartão de ponto na home')
    mostrar_popup_ferias = models.BooleanField(
        default=True, verbose_name='Mostrar o popup de férias')
    bloquear_navegacao_ferias = models.BooleanField(
        default=False, verbose_name='Bloquear o portal para quem está de férias')

    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Atualizado por')

    class Meta:
        verbose_name = 'Configuração do Tangerino'
        verbose_name_plural = 'Configuração do Tangerino'

    def __str__(self):
        return 'Configuração do módulo Ponto e Férias'

    def save(self, *args, **kwargs):
        self.pk = 1                       # singleton
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def libera(self, user):
        """O usuário enxerga o módulo?

        O superusuário é checado ANTES de `ativo` de propósito: a tela que
        religa o módulo vive dentro dele, então desligar com o superusuário
        junto seria uma chave de mão única, sem volta pela interface.
        """
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser:
            return True
        if not self.ativo:
            return False
        if not self.restrito_ao_grupo:
            return True
        if not self.grupo_id:
            return False
        return user.communication_groups.filter(id=self.grupo_id).exists()


class SincronizacaoTangerino(models.Model):
    """Registro de cada rodada de sincronização, para auditoria na tela admin."""

    class Tipo(models.TextChoices):
        VINCULO = 'VINCULO', 'Vínculo de funcionários'
        PONTO = 'PONTO', 'Marcações de ponto'
        FERIAS = 'FERIAS', 'Lançamentos de férias'

    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.VINCULO)
    executada_em = models.DateTimeField(auto_now_add=True)
    executada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sincronizacoes_tangerino', verbose_name='Executada por')

    casados_cpf = models.PositiveIntegerField(default=0, verbose_name='Casados por CPF')
    casados_nome = models.PositiveIntegerField(default=0, verbose_name='Casados por nome')
    ja_vinculados = models.PositiveIntegerField(default=0, verbose_name='Já vinculados')
    sem_correspondencia = models.PositiveIntegerField(default=0, verbose_name='Sem correspondência')
    criados = models.PositiveIntegerField(default=0, verbose_name='Registros criados')
    atualizados = models.PositiveIntegerField(default=0, verbose_name='Registros atualizados')
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


# ─── Espelho local dos dados do Tangerino ────────────────────────────────────
# Estas duas tabelas guardam o que a API devolve. Não substituem o Tangerino,
# que continua sendo a fonte oficial — servem para ter histórico no banco,
# permitir relatório/consulta sem depender da API no ar e comparar o que mudou
# entre uma sincronização e outra.

class MarcacaoPonto(models.Model):
    """Um par entrada/saída vindo do Tangerino.

    A API entrega o dia já pareado (`dateIn`/`dateOut`); a chave `tangerino_id`
    é a do próprio par, então uma nova sincronização atualiza o registro em vez
    de duplicá-lo — inclusive quando a saída é batida depois da entrada.
    """

    tangerino_id = models.BigIntegerField(unique=True, verbose_name='ID no Tangerino')
    employee_id = models.IntegerField(db_index=True, verbose_name='ID do funcionário')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='marcacoes_ponto', verbose_name='Usuário do portal')
    nome_funcionario = models.CharField(max_length=200, blank=True, verbose_name='Nome no Tangerino')

    dia = models.DateField(db_index=True, verbose_name='Dia')
    entrada = models.DateTimeField(null=True, blank=True, verbose_name='Entrada')
    saida = models.DateTimeField(null=True, blank=True, verbose_name='Saída')
    nsr_entrada = models.BigIntegerField(null=True, blank=True, verbose_name='NSR da entrada')
    nsr_saida = models.BigIntegerField(null=True, blank=True, verbose_name='NSR da saída')

    status = models.CharField(max_length=20, blank=True, verbose_name='Status')
    plataforma = models.CharField(max_length=30, blank=True, verbose_name='Plataforma')
    editado = models.BooleanField(default=False, verbose_name='Editado no Tangerino')
    ajuste = models.BooleanField(default=False, verbose_name='É ajuste/afastamento')
    observacao = models.TextField(blank=True, verbose_name='Observação')

    sincronizado_em = models.DateTimeField(auto_now=True, verbose_name='Sincronizado em')

    class Meta:
        verbose_name = 'Marcação de ponto (sincronizada)'
        verbose_name_plural = 'Marcações de ponto (sincronizadas)'
        ordering = ['-dia', '-entrada']
        indexes = [models.Index(fields=['employee_id', 'dia'])]

    def __str__(self):
        return f"{self.nome_funcionario or self.employee_id} — {self.dia:%d/%m/%Y}"

    @property
    def em_aberto(self):
        return bool(self.entrada and not self.saida)

    @property
    def segundos_trabalhados(self):
        if not (self.entrada and self.saida):
            return 0
        return max(0, int((self.saida - self.entrada).total_seconds()))


class FeriasLancamento(models.Model):
    """Um lançamento de FÉRIAS vindo do Tangerino."""

    tangerino_id = models.BigIntegerField(unique=True, verbose_name='ID no Tangerino')
    employee_id = models.IntegerField(db_index=True, verbose_name='ID do funcionário')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ferias_lancamentos', verbose_name='Usuário do portal')
    nome_funcionario = models.CharField(max_length=200, blank=True, verbose_name='Nome no Tangerino')

    inicio = models.DateField(db_index=True, verbose_name='Início')
    fim = models.DateField(db_index=True, verbose_name='Fim')
    status = models.CharField(max_length=20, blank=True, verbose_name='Status')
    observacao = models.TextField(blank=True, verbose_name='Observação')
    origem = models.CharField(max_length=40, blank=True, verbose_name='Origem')
    dia_inteiro = models.BooleanField(default=True, verbose_name='Dia inteiro')

    sincronizado_em = models.DateTimeField(auto_now=True, verbose_name='Sincronizado em')

    class Meta:
        verbose_name = 'Férias (sincronizada)'
        verbose_name_plural = 'Férias (sincronizadas)'
        ordering = ['-inicio']

    def __str__(self):
        return f"{self.nome_funcionario or self.employee_id}: {self.inicio:%d/%m/%Y}–{self.fim:%d/%m/%Y}"

    @property
    def dias(self):
        return max(1, (self.fim - self.inicio).days + 1)


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
    com_foto = models.BooleanField(default=False, verbose_name='Enviou foto')
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
