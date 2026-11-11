"""Contas a Pagar (BI): o que era o dados.db do sistema antigo, agora no Postgres do portal.

O histórico oficial (títulos programados e saldos publicados) continua no MySQL
``contas_pagar`` — é de lá que o Power BI lê. Aqui ficam só as configurações
(contas bancárias, categorias/TipoMacro, regra do Agging), os rascunhos do
"Pagar hoje", quem acessa e o registro de cada publicação/exclusão de dia.
"""
from django.conf import settings
from django.db import models

from . import regras


class AcessoContasPagar(models.Model):
    CONSULTA = 'CONSULTA'
    OPERADOR = 'OPERADOR'
    CONFIGURADOR = 'CONFIGURADOR'
    PAPEIS = [(CONSULTA, 'Consulta'), (OPERADOR, 'Operador'), (CONFIGURADOR, 'Configurador')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='acesso_contas_pagar', verbose_name='Pessoa')
    papel = models.CharField(max_length=14, choices=PAPEIS, default=CONSULTA, verbose_name='Papel')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Acesso ao Contas a Pagar'
        verbose_name_plural = 'Acessos ao Contas a Pagar'

    def __str__(self):
        return f'{self.user} — {self.get_papel_display()}'


class ContaBancaria(models.Model):
    """Conta da empresa. O "Banco pagador" de cada título sai da agência/conta do campo Débito."""

    banco = models.CharField(max_length=200, verbose_name='Banco')
    agencia = models.CharField(max_length=20, blank=True, default='', verbose_name='Agência')
    conta = models.CharField(max_length=30, blank=True, default='', verbose_name='Conta')
    ordem = models.IntegerField(default=0)
    ativo = models.BooleanField(default=True, verbose_name='Ativa')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['ordem', 'id']
        verbose_name = 'Conta bancária'
        verbose_name_plural = 'Contas bancárias'

    def __str__(self):
        return self.nome

    @property
    def nome(self):
        return regras.nome_banco(self.como_dict())

    def como_dict(self):
        return {'id': self.id, 'banco': self.banco, 'agencia': self.agencia or '', 'conta': self.conta or '',
                'ordem': self.ordem, 'ativo': self.ativo}


class Categoria(models.Model):
    """Aba "Dados": Tipo de conta (Despesa/Agging) → Categoria corrigida (TipoMacro)."""

    tipo = models.CharField(max_length=255, unique=True, verbose_name='Tipo de conta')
    categoria = models.CharField(max_length=120, verbose_name='Categoria')

    class Meta:
        ordering = ['tipo']
        verbose_name = 'Categoria (TipoMacro)'
        verbose_name_plural = 'Categorias (TipoMacro)'

    def __str__(self):
        return f'{self.tipo} → {self.categoria}'


class ConfiguracaoContasPagar(models.Model):
    """Regra da coluna Agging (uma linha só)."""

    agging_despesa_contem = models.CharField(max_length=120, default=regras.AGGING_DESPESA_PADRAO,
                                             verbose_name='Despesa contém')
    agging_fornecedor_contem = models.CharField(max_length=120, default=regras.AGGING_FORNECEDOR_PADRAO,
                                                verbose_name='Fornecedor contém')
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração do Contas a Pagar'
        verbose_name_plural = 'Configuração do Contas a Pagar'

    @classmethod
    def atual(cls):
        obj = cls.objects.order_by('id').first()
        return obj or cls.objects.create()

    def como_dict(self):
        return {'agging_despesa_contem': self.agging_despesa_contem,
                'agging_fornecedor_contem': self.agging_fornecedor_contem}


class Rascunho(models.Model):
    """Uma BASE VIVOGO importada e ainda não publicada. Some ao publicar ou descartar."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rascunhos_contas_pagar',
                             verbose_name='Quem importou')
    criado_em = models.DateTimeField(auto_now_add=True)
    arquivo = models.CharField(max_length=255, blank=True, default='')
    data_pagamento = models.DateField()
    venc_ini = models.DateField()
    venc_fim = models.DateField()
    qtd_linhas = models.IntegerField(default=0)

    class Meta:
        ordering = ['-id']
        verbose_name = 'Rascunho do pagar hoje'
        verbose_name_plural = 'Rascunhos do pagar hoje'

    def __str__(self):
        return f'{self.arquivo} ({self.criado_em:%d/%m/%Y %H:%M})'


class TituloRascunho(models.Model):
    rascunho = models.ForeignKey(Rascunho, on_delete=models.CASCADE, related_name='titulos')
    vencimento = models.DateField(null=True, blank=True)
    valor = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    excluido = models.BooleanField(default=False)
    dados = models.JSONField(default=dict)  # a linha do relatório, com os cabeçalhos originais

    class Meta:
        ordering = ['vencimento', 'valor', 'id']


class SaldoRascunho(models.Model):
    rascunho = models.ForeignKey(Rascunho, on_delete=models.CASCADE, related_name='saldos')
    conta = models.ForeignKey(ContaBancaria, on_delete=models.CASCADE, related_name='+')
    aplicacao = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    garantida = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    saldo = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    transferencia = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['rascunho', 'conta'], name='contas_pagar_saldo_unico')]


class LogContasPagar(models.Model):
    """Cada dia publicado no MySQL ou excluído dele: quem, quando, qual dia, quantos títulos, quanto."""

    PUBLICOU = 'PUBLICOU'
    EXCLUIU_DIA = 'EXCLUIU_DIA'
    ACOES = [(PUBLICOU, 'Publicou o dia'), (EXCLUIU_DIA, 'Excluiu o dia')]

    acao = models.CharField(max_length=12, choices=ACOES)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    em = models.DateTimeField(auto_now_add=True, db_index=True)
    data_pagamento = models.DateField(db_index=True)
    qtd_titulos = models.IntegerField(default=0)
    valor = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    arquivo = models.CharField(max_length=255, blank=True, default='')
    detalhe = models.JSONField(default=dict, blank=True)  # vencimentos, saldos, o que havia antes

    class Meta:
        ordering = ['-em', '-id']
        verbose_name = 'Registro do Contas a Pagar'
        verbose_name_plural = 'Registros do Contas a Pagar'

    def __str__(self):
        return f'{self.get_acao_display()} {self.data_pagamento:%d/%m/%Y} ({self.qtd_titulos} títulos)'
