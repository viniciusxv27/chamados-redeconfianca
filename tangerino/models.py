from datetime import time

from django.conf import settings
from django.db import models


def _hhmm(minutos, com_sinal=False):
    """Minutos em HH:MM. Horas de trabalho passam de 24h, então não usa data."""
    minutos = int(minutos or 0)
    sinal = '-' if minutos < 0 else ('+' if com_sinal else '')
    minutos = abs(minutos)
    return f"{sinal}{minutos // 60:02d}:{minutos % 60:02d}"


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

    # ── Regras de jornada ───────────────────────────────────────────────────
    # Cada uma tranca a navegação de alguém, então todas nascem desligadas e
    # são ligadas uma a uma, quando o RH pedir.
    bloquear_sem_entrada = models.BooleanField(
        default=False, verbose_name='Bloquear o portal antes da entrada do dia',
        help_text='Quem ainda não registrou a entrada só vê a tela de aviso.')
    bloquear_durante_almoco = models.BooleanField(
        default=False, verbose_name='Bloquear o portal durante o intervalo',
        help_text='Libera de volta assim que a volta do almoço for registrada.')
    bloquear_saida_pendente = models.BooleanField(
        default=False, verbose_name='Bloquear o portal com saída em aberto',
        help_text='Entrada de um dia anterior sem a saída correspondente.')
    avisar_almoco = models.BooleanField(
        default=False, verbose_name='Avisar sobre o intervalo',
        help_text='Popup de almoço esquecido e de intervalo passando do limite.')

    almoco_minimo_minutos = models.PositiveSmallIntegerField(
        default=60, verbose_name='Duração mínima do intervalo (minutos)',
        help_text='A volta do almoço é recusada antes disso.')
    almoco_maximo_minutos = models.PositiveSmallIntegerField(
        default=65, verbose_name='Intervalo considerado longo (minutos)',
        help_text='Acima disso o portal avisa que o almoço passou da hora.')
    lembrete_almoco_hora = models.TimeField(
        default=time(16, 0), verbose_name='Hora do lembrete de almoço',
        help_text='Se não houver saída para o intervalo até esta hora, avisa.')
    entrada_manha_de = models.TimeField(
        default=time(7, 0), verbose_name='Entrada da manhã — de',
        help_text='O lembrete de almoço só vale para quem entrou nesta faixa.')
    entrada_manha_ate = models.TimeField(
        default=time(10, 0), verbose_name='Entrada da manhã — até')

    # ── Sincronização automática ────────────────────────────────────────────
    # Produção roda só gunicorn: não há cron nem worker separado. Quem dispara
    # é a primeira requisição depois da hora marcada, e a corrida entre os 3
    # workers é resolvida por UPDATE condicional (ver tangerino/agendador.py).
    sincronizar_automatico = models.BooleanField(
        default=False, verbose_name='Sincronizar sozinho todo dia',
        help_text='Puxa jornadas, marcações, férias e saldo do Tangerino uma vez por dia.')
    hora_sincronizacao = models.TimeField(
        default=time(7, 0), verbose_name='Hora da sincronização automática',
        help_text='A primeira visita ao portal a partir deste horário dispara.')
    dias_sincronizacao = models.PositiveSmallIntegerField(
        default=30, verbose_name='Dias de ponto para trás',
        help_text='Janela de marcações que a sincronização automática recarrega.')
    ultima_sincronizacao_automatica = models.DateTimeField(
        null=True, blank=True, verbose_name='Última sincronização automática')

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
        # Quem administra o módulo é checado ANTES de `ativo`, pela mesma razão
        # do superusuário: a tela que religa o módulo vive dentro dele. Sem
        # isso, desligar seria chave de mão única para a ADMINISTRAÇÃO.
        if user.is_superuser or getattr(user, 'can_manage_rh', lambda: False)():
            return True
        if not self.ativo:
            return False
        # Liberação individual (SUPERADMIN na tela do usuário) fura a restrição
        # de grupo, mas continua respeitando o módulo estar ativo.
        from users.module_access import user_has_module
        if user_has_module(user, 'ponto'):
            return True
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
        SALDO = 'SALDO', 'Saldo de banco de horas'
        JORNADA = 'JORNADA', 'Jornadas contratadas'

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
    """O dia de ponto de uma pessoa em **uma linha só**.

    A API entrega o dia fatiado em pares entrada/saída; aqui eles são achatados
    em colunas, que é como se lê um cartão de ponto: entrada1/saída1 (manhã),
    entrada2/saída2 (tarde).

    O terceiro par existe porque a realidade tem 0,7% de dias assim — turno que
    vira a madrugada, retorno tarde da noite. Sem ele, essas marcações sumiriam
    em silêncio. E se algum dia aparecer um quarto par, ele vai para
    ``marcacoes_extras`` em vez de ser descartado.

    A chave é (funcionário, dia): ressincronizar o mesmo período atualiza a
    linha em vez de duplicar.
    """

    employee_id = models.IntegerField(db_index=True, verbose_name='ID do funcionário')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='marcacoes_ponto', verbose_name='Usuário do portal')
    nome = models.CharField(max_length=200, blank=True, db_index=True, verbose_name='Nome')
    data = models.DateField(db_index=True, verbose_name='Data')

    entrada1 = models.DateTimeField(null=True, blank=True, verbose_name='Entrada 1')
    saida1 = models.DateTimeField(null=True, blank=True, verbose_name='Saída 1')
    entrada2 = models.DateTimeField(null=True, blank=True, verbose_name='Entrada 2')
    saida2 = models.DateTimeField(null=True, blank=True, verbose_name='Saída 2')
    entrada3 = models.DateTimeField(null=True, blank=True, verbose_name='Entrada 3')
    saida3 = models.DateTimeField(null=True, blank=True, verbose_name='Saída 3')
    marcacoes_extras = models.JSONField(
        default=list, blank=True, verbose_name='Marcações além do 3º par',
        help_text='Rede de segurança: nada é descartado se o dia tiver mais pares.')

    total_segundos = models.PositiveIntegerField(default=0, verbose_name='Trabalhado (segundos)')
    # Quanto a escala contratada previa para esse dia, já sem feriado, férias e
    # abonos. Zero em folga. Guardado por dia porque a escala pode mudar e o
    # previsto de um dia passado tem que continuar sendo o daquele dia.
    previsto_segundos = models.PositiveIntegerField(
        default=0, verbose_name='Previsto (segundos)')
    em_aberto = models.BooleanField(default=False, verbose_name='Tem entrada sem saída')
    plataforma = models.CharField(max_length=30, blank=True, verbose_name='Plataforma')
    editado = models.BooleanField(default=False, verbose_name='Editado no Tangerino')
    tangerino_ids = models.JSONField(default=list, blank=True,
                                     verbose_name='IDs dos pares no Tangerino')
    sincronizado_em = models.DateTimeField(verbose_name='Sincronizado em')

    class Meta:
        verbose_name = 'Ponto do dia (sincronizado)'
        verbose_name_plural = 'Pontos do dia (sincronizados)'
        ordering = ['-data', 'nome']
        constraints = [
            models.UniqueConstraint(fields=['employee_id', 'data'],
                                    name='tangerino_ponto_unico_por_dia'),
        ]
        indexes = [models.Index(fields=['data', 'nome'])]

    def __str__(self):
        return f"{self.nome or self.employee_id} — {self.data:%d/%m/%Y}"

    @property
    def total_hhmm(self):
        s = self.total_segundos or 0
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"

    @property
    def horarios(self):
        """Marcações do dia em ordem, já formatadas — para listar na tela."""
        campos = (self.entrada1, self.saida1, self.entrada2,
                  self.saida2, self.entrada3, self.saida3)
        return [d.strftime('%H:%M') for d in campos if d]


class JornadaTrabalho(models.Model):
    """Espelho de uma escala contratada do Tangerino (``/work-schedule/{id}``).

    Guardada localmente porque é a base do "quantas horas deveria ter
    trabalhado": são ~30 escalas para 170 pessoas, e consultar a API a cada
    tela seria caro para um dado que muda uma vez por ano.

    ``horas_por_dia`` usa a numeração do Tangerino (1 = domingo … 7 = sábado);
    dia ausente é folga.
    """

    tangerino_id = models.IntegerField(unique=True, verbose_name='ID no Tangerino')
    nome = models.CharField(max_length=200, blank=True, verbose_name='Nome da escala')
    horas_por_dia = models.JSONField(
        default=dict, blank=True, verbose_name='Segundos previstos por dia da semana')
    segundos_semana = models.PositiveIntegerField(
        default=0, verbose_name='Total previsto na semana (segundos)')
    sincronizado_em = models.DateTimeField(verbose_name='Sincronizado em')

    class Meta:
        verbose_name = 'Jornada contratada (sincronizada)'
        verbose_name_plural = 'Jornadas contratadas (sincronizadas)'
        ordering = ['nome']

    def __str__(self):
        return f"{self.nome or self.tangerino_id} ({self.horas_semana}h/semana)"

    @property
    def horas_semana(self):
        return round((self.segundos_semana or 0) / 3600, 1)

    @property
    def registra_ponto(self):
        """Escala sem nenhuma hora é de quem não bate ponto."""
        return bool(self.segundos_semana)

    def segundos_no_dia(self, dia_tangerino):
        """Aceita a chave como int ou string — o JSON guarda string."""
        grade = self.horas_por_dia or {}
        return grade.get(str(dia_tangerino)) or grade.get(dia_tangerino) or 0

    @property
    def resumo_semana(self):
        """A semana em texto: 'seg 8h · ter 7h20 · … · sáb 4h'."""
        nomes = {1: 'dom', 2: 'seg', 3: 'ter', 4: 'qua', 5: 'qui', 6: 'sex', 7: 'sáb'}
        partes = []
        for dia in range(1, 8):
            segundos = self.segundos_no_dia(dia)
            if not segundos:
                continue
            horas, minutos = divmod(int(segundos) // 60, 60)
            partes.append(f"{nomes[dia]} {horas}h{minutos:02d}" if minutos
                          else f"{nomes[dia]} {horas}h")
        return ' · '.join(partes) or 'sem jornada'


class SaldoHoras(models.Model):
    """Saldo de banco de horas por pessoa, calculado pelo próprio Tangerino.

    O número vem pronto do endpoint deles (``hoursBalanceInMinutes``) em vez de
    ser recalculado aqui: o saldo depende da escala contratada de cada um, que
    a API não expõe. Refazer a conta por fora daria um número diferente do que
    o colaborador vê no app — e num assunto de banco de horas, dois números
    divergentes é pior do que nenhum.

    O saldo é **sempre relativo a um período**: a mesma pessoa tem saldo
    diferente em 30 dias e no ano. Por isso o período fica gravado na linha —
    sem ele o número não significa nada.
    """

    employee_id = models.IntegerField(unique=True, verbose_name='ID do funcionário')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='saldos_horas', verbose_name='Usuário do portal')
    nome = models.CharField(max_length=200, blank=True, db_index=True, verbose_name='Nome')
    email = models.EmailField(blank=True, verbose_name='E-mail no Tangerino')

    saldo_minutos = models.IntegerField(
        default=0, verbose_name='Saldo (minutos)',
        help_text='Positivo = horas a favor do colaborador; negativo = horas devidas.')

    # A conta por trás do saldo. O Tangerino não devolve estes dois números:
    # o previsto vem da escala contratada e o trabalhado, da soma das
    # marcações. Servem para mostrar de onde o saldo veio — o saldo oficial
    # continua sendo `saldo_minutos`, que é o que o colaborador vê no app.
    previsto_minutos = models.IntegerField(
        default=0, verbose_name='Previsto no período (minutos)',
        help_text='Jornada contratada no período, descontados feriados, férias e abonos.')
    trabalhado_minutos = models.IntegerField(
        default=0, verbose_name='Trabalhado no período (minutos)',
        help_text='Soma das marcações registradas no período.')
    # Previsto e trabalhado saem das marcações já espelhadas no banco, que
    # cobrem uma janela menor que o saldo (o histórico inteiro não cabe numa
    # consulta à API). A janela fica gravada para o número não ser lido como
    # se fosse do período todo.
    analise_inicio = models.DateField(
        null=True, blank=True, verbose_name='Previsto/trabalhado — início')
    analise_fim = models.DateField(
        null=True, blank=True, verbose_name='Previsto/trabalhado — fim')

    periodo_inicio = models.DateField(verbose_name='Período — início')
    periodo_fim = models.DateField(verbose_name='Período — fim')
    sincronizado_em = models.DateTimeField(verbose_name='Sincronizado em')

    class Meta:
        verbose_name = 'Saldo de horas (sincronizado)'
        verbose_name_plural = 'Saldos de horas (sincronizados)'
        ordering = ['nome']

    def __str__(self):
        return f"{self.nome or self.employee_id}: {self.saldo_hhmm}"

    @property
    def saldo_hhmm(self):
        """Saldo em +HH:MM / -HH:MM, que é como banco de horas se lê."""
        minutos = self.saldo_minutos or 0
        horas, resto = divmod(abs(minutos), 60)
        return f"{'-' if minutos < 0 else '+'}{horas}:{resto:02d}"

    @property
    def saldo_horas(self):
        return round((self.saldo_minutos or 0) / 60, 2)

    @property
    def previsto_hhmm(self):
        return _hhmm(self.previsto_minutos)

    @property
    def trabalhado_hhmm(self):
        return _hhmm(self.trabalhado_minutos)

    @property
    def diferenca_minutos(self):
        """Trabalhado menos previsto — a conta que dá para conferir na tela.

        Não é o mesmo que ``saldo_minutos``: o saldo oficial do Tangerino
        também considera compensações e acordos que a API não expõe. Quando os
        dois divergem muito, a diferença está aí.
        """
        return (self.trabalhado_minutos or 0) - (self.previsto_minutos or 0)

    @property
    def diferenca_hhmm(self):
        return _hhmm(self.diferenca_minutos, com_sinal=True)

    @property
    def aproveitamento(self):
        """Percentual do previsto que foi de fato trabalhado."""
        if not self.previsto_minutos:
            return None
        return round((self.trabalhado_minutos or 0) / self.previsto_minutos * 100)

    @property
    def tem_analise(self):
        return bool(self.analise_inicio and self.analise_fim)

    @property
    def devedor(self):
        return (self.saldo_minutos or 0) < 0


class FeriasLancamento(models.Model):
    """Um lançamento de FÉRIAS vindo do Tangerino."""

    tangerino_id = models.BigIntegerField(unique=True, verbose_name='ID no Tangerino')
    employee_id = models.IntegerField(db_index=True, verbose_name='ID do funcionário')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ferias_lancamentos', verbose_name='Usuário do portal')
    nome = models.CharField(max_length=200, blank=True, db_index=True, verbose_name='Nome')

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
        return f"{self.nome or self.employee_id}: {self.inicio:%d/%m/%Y}–{self.fim:%d/%m/%Y}"

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
    # Guardados para conseguir responder "a foto/localização chegaram?" sem
    # precisar bater outro ponto de teste — foi exatamente o que faltou da
    # primeira vez que a marcação saiu sem foto e sem local.
    foto_url = models.URLField(blank=True, max_length=500, verbose_name='URL da foto no Tangerino')
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
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


# ─────────────────────────────────────────────────────────────────────────────
# Isenção de ponto
# ─────────────────────────────────────────────────────────────────────────────
# Grupo de comunicação (/users/manage/groups/) que reúne quem não bate ponto.
# Fica pelo ID porque é ele que identifica o grupo de forma estável: renomear
# o grupo na tela não deve desligar a isenção sem ninguém perceber.
GRUPO_SEM_PONTO_ID = 41          # "Não bate ponto"


def nao_bate_ponto(user):
    """A pessoa está dispensada de bater ponto?

    Quem está no grupo não é bloqueado pela jornada e não é avaliado por
    assiduidade — cobrar marcação de quem não precisa marcar seria zerar a nota
    de alguém por não fazer algo que ninguém pediu.

    Falha para ``False``: na dúvida, a regra normal continua valendo. Uma
    consulta que quebra não pode virar isenção silenciosa para a rede inteira.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    try:
        return user.communication_groups.filter(pk=GRUPO_SEM_PONTO_ID).exists()
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Escala (montada no portal pelos gerentes)
# ─────────────────────────────────────────────────────────────────────────────
# Diferente da jornada CONTRATADA (JornadaTrabalho, que vem do Tangerino), a
# escala é o planejamento SEMANAL que o gerente da loja monta para a equipe:
# em cada dia, entrada e saída — ou folga. Nada disto vai para o Tangerino; é o
# quadro de horários da loja, visível para o colaborador e para a gestão.

class EscalaConfig(models.Model):
    """Registro único (id=1): quem, além do SUPERADMIN, gere todas as escalas.

    O SUPERADMIN indica aqui os "gestores globais" — pessoas que enxergam e
    editam a escala de qualquer setor, sem serem gerentes de uma loja.
    """
    gestores = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='escala_gestor_de',
        verbose_name='Gestores globais de escala',
        help_text='Além do SUPERADMIN, enxergam e editam a escala de todos os setores.')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração de Escala'
        verbose_name_plural = 'Configuração de Escala'

    def __str__(self):
        return 'Configuração de Escala'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Escala(models.Model):
    """A escala de UMA pessoa em UMA semana (segunda a domingo).

    A semana é identificada pela segunda-feira (``semana_inicio``), o que evita
    a ambiguidade de "semana do mês" (começa no dia 1? na primeira segunda?): a
    tela mostra o rótulo amigável, mas o banco guarda a data, que é exata.
    """
    colaborador = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='escalas', verbose_name='Colaborador')
    semana_inicio = models.DateField(
        db_index=True, verbose_name='Início da semana (segunda-feira)')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='escalas_criadas', verbose_name='Criada por')
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='escalas_atualizadas', verbose_name='Atualizada por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Escala'
        verbose_name_plural = 'Escalas'
        ordering = ['-semana_inicio', 'colaborador__first_name']
        constraints = [
            models.UniqueConstraint(
                fields=['colaborador', 'semana_inicio'],
                name='escala_unica_por_pessoa_semana'),
        ]

    def __str__(self):
        return f"{self.colaborador} — semana de {self.semana_inicio:%d/%m/%Y}"


# Jornada semanal da CLT usada como meta na tela da escala.
HORAS_SEMANAIS = 44


def minutos_do_dia(entrada, saida_almoco, volta_almoco, saida, folga=False):
    """Minutos entre os horários informados, descontando o intervalo do almoço.

    Aceita a escala pela metade: quem só preencheu entrada e saída tem o dia
    inteiro contado; quem preencheu o almoço tem os dois turnos somados. Falta
    de par (entrada sem saída) simplesmente não conta — é escala incompleta,
    não erro.
    """
    if folga:
        return 0

    def intervalo(ini, fim):
        if not ini or not fim:
            return 0
        i = ini.hour * 60 + ini.minute
        f = fim.hour * 60 + fim.minute
        if f < i:                      # virou a meia-noite
            f += 24 * 60
        return f - i

    if saida_almoco and volta_almoco:
        return intervalo(entrada, saida_almoco) + intervalo(volta_almoco, saida)
    return intervalo(entrada, saida)


class EscalaDia(models.Model):
    """Um dia da escala: entrada e saída, ou folga."""
    escala = models.ForeignKey(
        Escala, on_delete=models.CASCADE, related_name='dias', verbose_name='Escala')
    data = models.DateField(verbose_name='Dia')
    entrada = models.TimeField(null=True, blank=True, verbose_name='Entrada')
    saida_almoco = models.TimeField(
        null=True, blank=True, verbose_name='Saída para o almoço')
    volta_almoco = models.TimeField(
        null=True, blank=True, verbose_name='Volta do almoço')
    saida = models.TimeField(null=True, blank=True, verbose_name='Saída')
    folga = models.BooleanField(default=False, verbose_name='Folga')
    observacao = models.CharField(
        max_length=120, blank=True, default='', verbose_name='Observação')

    class Meta:
        verbose_name = 'Dia da escala'
        verbose_name_plural = 'Dias da escala'
        ordering = ['data']
        constraints = [
            models.UniqueConstraint(fields=['escala', 'data'], name='escala_dia_unico'),
        ]

    def __str__(self):
        if self.folga:
            return f"{self.data:%d/%m}: folga"
        if self.entrada and self.saida:
            return f"{self.data:%d/%m}: {self.entrada:%H:%M}–{self.saida:%H:%M}"
        return f"{self.data:%d/%m}"

    @property
    def preenchido(self):
        """Tem algo que valha a pena guardar (folga ou algum horário)."""
        return bool(self.folga or self.entrada or self.saida
                    or self.saida_almoco or self.volta_almoco or self.observacao)

    @property
    def minutos(self):
        """Minutos trabalhados no dia, já descontado o almoço.

        Turno que vira a meia-noite conta certo: a saída menor que a entrada é
        lida como do dia seguinte, não como número negativo.
        """
        return minutos_do_dia(self.entrada, self.saida_almoco,
                              self.volta_almoco, self.saida, self.folga)

    @property
    def horas(self):
        return self.minutos / 60
