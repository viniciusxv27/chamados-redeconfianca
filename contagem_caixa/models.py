"""Contagem de Caixa: controle diário de valores por loja.

Uma linha por loja/dia, no mesmo formato da planilha que o financeiro já usa:

    Data · Valor SAP · Vivo go EA · DIVERG. · STATUS · allied · recarga ·
    Agoracred · Renova · sangria/erro · Transferências · Valor real ·
    Entrada · Diferença · Depósito · saldo

O **Valor SAP** vem da importação diária da base de vendas. Os demais são
preenchidos na tela. O que o sistema calcula sozinho — e por isso não é
editável — está nas properties: divergência, status, diferença e saldo.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

from users.models import Sector

ZERO = Decimal('0.00')

# ── As três contas do caixa ─────────────────────────────────────────────────
# Estão aqui, com nome, porque são a régua do financeiro: mudar uma delas muda
# o que a loja deve ter na gaveta.

# Some no SAP mas não vira dinheiro na gaveta: serviço de parceiro, faturado
# junto e repassado depois.
COLUNAS_NAO_SAO_CAIXA = ('allied', 'recarga', 'agoracred', 'renova')

# Fora da comparação com o Vivo go: o Vivo go não registra estes lançamentos,
# então mantê-los no SAP faria toda loja parecer divergente todo dia.
COLUNAS_FORA_DA_DIVERGENCIA = ('agoracred', 'renova', 'transferencias')


def _dec(campo_verbose, **extra):
    return models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO,
        verbose_name=campo_verbose, **extra)


class ContagemCaixaDia(models.Model):
    """O dia de caixa de uma loja."""

    class Status(models.TextChoices):
        OK = 'OK', 'OK'
        ATENCAO = 'ATENCAO', 'Atenção'
        PENDENTE = 'PENDENTE', 'A contar'
        SEM_MOVIMENTO = 'SEM_MOVIMENTO', 'Sem movimento'

    loja = models.ForeignKey(
        Sector, on_delete=models.CASCADE, related_name='contagens_caixa',
        verbose_name='Loja')
    data = models.DateField(db_index=True, verbose_name='Data')

    # Vem da importação da base de vendas.
    valor_sap = _dec('Valor SAP')
    importado_em = models.DateTimeField(null=True, blank=True, verbose_name='Importado em')

    # Preenchidos na tela. O Vivo go aceita vazio de propósito: dia em branco
    # é dia que a loja ainda não contou, e isso não é a mesma coisa que ter
    # contado e dado zero — só o segundo caso pode virar divergência.
    valor_vivogo = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name='Vivo go EA')
    allied = _dec('allied')
    recarga = _dec('recarga')
    agoracred = _dec('Agoracred')
    renova = _dec('Renova')
    sangria_erro = _dec('sangria/erro')
    transferencias = _dec('Transferências')
    valor_real = _dec('Valor real')
    # A Entrada virou conta (ver a property `entrada`). O campo antigo continua
    # aqui para não descartar o que já tiver sido digitado à mão.
    entrada_manual = _dec('Entrada (digitada)')
    deposito = _dec('Depósito')
    observacao = models.TextField(blank=True, verbose_name='Observação')

    # Saldo é acumulado: guardado para não recalcular a série toda a cada tela.
    saldo = _dec('Saldo')

    # Aviso ao gerente quando o dia fica em ATENÇÃO. Guardado para não
    # notificar a mesma divergência todo dia.
    notificado_em = models.DateTimeField(null=True, blank=True, verbose_name='Gerente avisado em')

    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='contagens_caixa_atualizadas', verbose_name='Atualizado por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Contagem de caixa (dia)'
        verbose_name_plural = 'Contagem de caixa (dias)'
        ordering = ['loja__name', '-data']
        constraints = [
            models.UniqueConstraint(fields=['loja', 'data'],
                                    name='contagem_caixa_unica_por_loja_dia'),
        ]
        indexes = [models.Index(fields=['data', 'loja'])]

    def __str__(self):
        return f'{self.loja.name} — {self.data:%d/%m/%Y}'

    # ── Campos calculados ───────────────────────────────────────────────────
    @property
    def contado(self):
        """A loja já lançou o Vivo go deste dia?"""
        return self.valor_vivogo is not None

    def _soma(self, colunas):
        return sum((getattr(self, c) or ZERO) for c in colunas)

    @property
    def sap_comparavel(self):
        """O SAP na mesma régua do Vivo go.

        Agoracred, Renova e Transferências entram no SAP mas não aparecem no
        Vivo go. Comparar sem descontá-los acusaria divergência todo dia em
        toda loja — e um alerta que sempre dispara deixa de ser alerta.
        """
        return (self.valor_sap or ZERO) - self._soma(COLUNAS_FORA_DA_DIVERGENCIA)

    @property
    def entrada(self):
        """O que de fato entrou na gaveta.

        Do que o SAP faturou saem os serviços de parceiro (que são repasse, não
        dinheiro em caixa), a sangria e as transferências — dinheiro que saiu
        antes mesmo de ser contado.
        """
        return ((self.valor_sap or ZERO)
                - self._soma(COLUNAS_NAO_SAO_CAIXA)
                - (self.sangria_erro or ZERO)
                - (self.transferencias or ZERO))

    @property
    def divergencia(self):
        """SAP comparável menos Vivo go EA. Negativo quando o Vivo go veio maior.

        Sem contagem não há divergência: o dia está em branco, não errado.
        """
        if not self.contado:
            return ZERO
        return self.sap_comparavel - self.valor_vivogo

    @property
    def status(self):
        """OK, Atenção, A contar ou Sem movimento.

        Só vira Atenção quando a loja contou e o número não bateu — dia ainda
        não contado fica pendente, senão o gerente receberia alerta de todo dia
        que ninguém abriu ainda.
        """
        if not self.contado:
            return (self.Status.PENDENTE if (self.valor_sap or ZERO) != ZERO
                    else self.Status.SEM_MOVIMENTO)
        return self.Status.ATENCAO if self.divergencia != ZERO else self.Status.OK

    @property
    def status_rotulo(self):
        return dict(self.Status.choices).get(self.status, self.status)

    @property
    def em_atencao(self):
        return self.status == self.Status.ATENCAO

    @property
    def diferenca(self):
        """Entrada menos valor real — é o que sai (ou entra) do saldo do dia."""
        return (self.entrada or ZERO) - (self.valor_real or ZERO)

    def calcular_saldo(self, saldo_anterior):
        """Saldo do dia = saldo anterior + valor real − depósito.

        O que foi depositado saiu da gaveta e foi para o banco; continuar
        somando no saldo faria o caixa parecer ter dinheiro que não tem.
        """
        return ((saldo_anterior or ZERO)
                + (self.valor_real or ZERO)
                - (self.deposito or ZERO))


class ConfiguracaoContagem(models.Model):
    """Como ler a base analítica de vendas para chegar no Valor SAP do dia.

    Numa contagem de caixa o que interessa é o **dinheiro**, não o faturamento.
    A base analítica não tem uma coluna "dinheiro": a forma de pagamento vem
    escrita dentro de ``DS_COND_PGTO_01/02/03`` ("DINHEIRO - R$ 300,00") e a
    mesma condição se repete em cada linha de produto do pedido — por isso a
    leitura deduplica por número de pedido antes de somar.

    O modo COLUNA existe para quando o financeiro quiser somar outra coisa
    (faturamento cheio, por exemplo). Nos dois casos a tela mostra a prévia
    antes de gravar: recorte errado num controle de caixa é pior do que não
    importar.
    """

    class Modo(models.TextChoices):
        FORMA_PGTO = 'FORMA_PGTO', 'Somar uma forma de pagamento (DS_COND_PGTO)'
        COLUNA = 'COLUNA', 'Somar uma coluna de valor'

    modo = models.CharField(
        max_length=12, choices=Modo.choices, default=Modo.FORMA_PGTO,
        verbose_name='Como calcular o Valor SAP')
    forma_pagamento = models.CharField(
        max_length=30, default='DINHEIRO', verbose_name='Forma de pagamento',
        help_text='Usada no modo por forma de pagamento. Ex.: DINHEIRO, PIX, DÉBITO, CRÉDITO.')
    colunas_condicao = models.CharField(
        max_length=200, default='DS_COND_PGTO_01,DS_COND_PGTO_02,DS_COND_PGTO_03',
        verbose_name='Colunas de condição de pagamento',
        help_text='Separadas por vírgula.')
    coluna_pedido = models.CharField(
        max_length=60, default='NU_ORDM_PRDD', verbose_name='Coluna do pedido',
        help_text='Usada para não contar o mesmo pagamento duas vezes.')

    coluna_valor = models.CharField(
        max_length=60, default='VALOR_NF', verbose_name='Coluna do valor',
        help_text='Somada como Valor SAP no modo por coluna.')
    coluna_codigo = models.CharField(
        max_length=60, default='CD_CRDN', verbose_name='Coluna do código da loja',
        help_text='Casada com o ADABAS do setor. É o casamento mais confiável.')
    coluna_loja = models.CharField(
        max_length=60, default='NOME LOJAS', verbose_name='Coluna do nome da loja')
    coluna_data = models.CharField(
        max_length=60, default='DATA', verbose_name='Coluna da data')
    aba = models.CharField(max_length=60, default='Export', verbose_name='Aba da planilha')

    filtro_coluna = models.CharField(
        max_length=60, blank=True, verbose_name='Filtrar pela coluna',
        help_text='Opcional. Ex.: CENARIO para importar só um cenário.')
    filtro_valor = models.CharField(
        max_length=120, blank=True, verbose_name='Filtrar pelo valor',
        help_text='Valor que a coluna acima precisa ter. Vazio = sem filtro.')

    notificar_gerente = models.BooleanField(
        default=True, verbose_name='Avisar o gerente quando ficar em Atenção')

    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração da contagem de caixa'
        verbose_name_plural = 'Configuração da contagem de caixa'

    def __str__(self):
        return 'Configuração da Contagem de Caixa'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ImportacaoContagem(models.Model):
    """Histórico de cada importação, para auditoria."""

    arquivo = models.CharField(max_length=255, blank=True, verbose_name='Arquivo')
    executada_em = models.DateTimeField(auto_now_add=True)
    executada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='importacoes_contagem', verbose_name='Importado por')

    linhas_lidas = models.PositiveIntegerField(default=0)
    dias_criados = models.PositiveIntegerField(default=0)
    dias_atualizados = models.PositiveIntegerField(default=0)
    lojas_sem_setor = models.TextField(blank=True, verbose_name='Lojas sem setor no portal')
    sucesso = models.BooleanField(default=True)
    detalhe = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Importação da contagem de caixa'
        verbose_name_plural = 'Importações da contagem de caixa'
        ordering = ['-executada_em']

    def __str__(self):
        return f'Importação de {self.executada_em:%d/%m/%Y %H:%M}'


class SaldoInicialMes(models.Model):
    """Com quanto o caixa da loja começa o mês.

    Sem nenhuma linha aqui, o comportamento é o de sempre: o mês começa com o
    saldo final do mês anterior — o dinheiro simplesmente continua na gaveta.
    Isso cobre o caso normal e continua sendo o padrão.

    A linha existe para os dois casos em que a corrente precisa ser cortada:

    * **começo de uso** — a loja entra no controle no meio do ano e o saldo
      anterior no portal é zero, mas a gaveta não está vazia;
    * **acerto de fechamento** — o financeiro fechou o mês num valor e o
      acumulado do portal ficou diferente por lançamento antigo corrigido
      depois. Sem poder fixar a abertura, o erro se arrastaria para sempre.

    Quem define é gestor: mexer aqui desloca o saldo de todos os dias dali para
    frente.
    """

    loja = models.ForeignKey(
        Sector, on_delete=models.CASCADE, related_name='saldos_iniciais_caixa',
        verbose_name='Loja')
    ano = models.PositiveSmallIntegerField(verbose_name='Ano')
    mes = models.PositiveSmallIntegerField(verbose_name='Mês')
    valor = _dec('Saldo inicial do mês')

    motivo = models.CharField(
        max_length=200, blank=True, verbose_name='Motivo',
        help_text='Aparece na tela. Ex.: "abertura do controle" ou "acerto do fechamento".')
    definido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='saldos_iniciais_caixa', verbose_name='Definido por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Saldo inicial do mês'
        verbose_name_plural = 'Saldos iniciais do mês'
        ordering = ['loja__name', '-ano', '-mes']
        constraints = [
            models.UniqueConstraint(fields=['loja', 'ano', 'mes'],
                                    name='contagem_caixa_saldo_inicial_unico'),
        ]

    def __str__(self):
        return f'{self.loja.name} — {self.mes:02d}/{self.ano}: {self.valor}'

    @classmethod
    def do_mes(cls, loja_id, ano, mes):
        """A linha daquele mês, ou None se o mês puxa do anterior."""
        return cls.objects.filter(loja_id=loja_id, ano=ano, mes=mes).first()

    @classmethod
    def mapa_da_loja(cls, loja_id):
        """{(ano, mes): valor} — usado no recálculo da corrente de saldos."""
        return {(s.ano, s.mes): s.valor
                for s in cls.objects.filter(loja_id=loja_id)}
