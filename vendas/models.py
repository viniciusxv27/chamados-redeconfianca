from decimal import Decimal

from django.conf import settings
from django.db import models


class ItemPreco(models.Model):
    """Item da tabela de preços (modelo genérico e flexível).

    Consolida as abas principais da planilha oficial (PLANOS, PRODUTOS,
    SMARTPHONES, ELETRÔNICOS, WATCHES...) numa estrutura única. Colunas que não
    têm campo próprio ficam em ``extra`` (JSON). Serve tanto para importação
    quanto para cadastro manual e é referenciado nos itens da venda.
    """

    categoria = models.CharField(max_length=60, db_index=True, verbose_name='Categoria (aba)')
    nome = models.CharField(max_length=200, verbose_name='Nome')
    plano = models.CharField(max_length=200, blank=True, verbose_name='Plano')
    sistema = models.CharField(max_length=60, blank=True, verbose_name='Sistema')
    grupamento = models.CharField(max_length=120, blank=True, verbose_name='Grupamento')
    cod_sap = models.CharField(max_length=40, blank=True, verbose_name='Cód. SAP')
    cod_sistema = models.CharField(max_length=40, blank=True, verbose_name='Cód. Sistema')
    valor = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Valor (R$)')
    extra = models.JSONField(default=dict, blank=True, verbose_name='Outros campos')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')
    importado_em = models.DateTimeField(null=True, blank=True, verbose_name='Importado em')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Item da Tabela de Preços'
        verbose_name_plural = 'Tabela de Preços'
        ordering = ['categoria', 'nome']
        indexes = [models.Index(fields=['categoria', 'nome'])]

    def __str__(self):
        return f'[{self.categoria}] {self.nome}'


class Venda(models.Model):
    """Cabeçalho da venda (espelha a tela 'Lançar venda'). Os itens ficam em
    VendaProduto/VendaServico. Grava apenas no Postgres (nada no MySQL)."""

    COMPROVANTE_CHOICES = [
        ('NFCE', 'NFC-e (Cupom Fiscal)'),
        ('NFE', 'NF-e (DANFE)'),
    ]

    loja = models.ForeignKey(
        'users.Sector', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vendas', verbose_name='Loja / PDV',
    )
    pdv_nome = models.CharField(max_length=120, blank=True, verbose_name='PDV (texto)')
    uf = models.CharField(max_length=2, blank=True, verbose_name='UF')
    vendedor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vendas_realizadas', verbose_name='Vendedor',
    )
    estoque_avancado = models.BooleanField(default=False, verbose_name='Venda Estoque Avançado?')
    cliente_nome = models.CharField(max_length=200, blank=True, verbose_name='Cliente')
    cliente_cpf = models.CharField(max_length=20, blank=True, verbose_name='CPF/CNPJ do cliente')
    tipo_venda = models.CharField(max_length=80, blank=True, verbose_name='Tipo de Venda')
    comprovante_fiscal = models.CharField(
        max_length=8, choices=COMPROVANTE_CHOICES, default='NFCE', verbose_name='Comprovante Fiscal',
    )
    data_venda = models.DateTimeField(verbose_name='Data da venda')
    observacao = models.TextField(blank=True, verbose_name='Observação')

    # SLV — Documento de Requisitos v1.0 (out/2026).
    cliente = models.ForeignKey('Cliente', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='vendas', verbose_name='Cliente (cadastro)')
    cliente_telefone = models.CharField(max_length=20, blank=True, default='', db_default='', verbose_name='Telefone do cliente')
    forma_pagamento = models.CharField(max_length=10, blank=True, default='', db_default='',
                                       choices=[('CARTAO', 'Cartão'), ('PIX', 'Pix')], verbose_name='Forma de pagamento')
    vivo_mais = models.BooleanField(default=False, db_default=False, verbose_name='Pago pelo Vivo+')
    vivo_mais_percentual = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                               verbose_name='Percentual Vivo+ (na época)')
    plano_anterior = models.ForeignKey('Plano', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', verbose_name='Plano anterior do cliente')
    plano_anterior_nome = models.CharField(max_length=200, blank=True, default='', db_default='', verbose_name='Plano anterior')
    segmentacao_anterior = models.CharField(max_length=10, blank=True, default='', db_default='', verbose_name='Segmentação anterior')
    valor_anterior = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                         verbose_name='Valor que o cliente pagava')
    numero_fake = models.BooleanField(default=False, db_default=False, db_index=True, verbose_name='Tem número fictício')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vendas_lancadas', verbose_name='Lançado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Venda'
        verbose_name_plural = 'Vendas'
        ordering = ['-data_venda', '-created_at']
        indexes = [
            models.Index(fields=['data_venda']),
            models.Index(fields=['loja']),
            models.Index(fields=['vendedor']),
        ]

    def __str__(self):
        return f'Venda #{self.pk} — {self.cliente_nome or "s/ cliente"}'

    @property
    def total_produtos(self):
        return sum((p.valor_total for p in self.produtos.all()), Decimal('0'))

    @property
    def total_servicos(self):
        return sum((s.valor_plano or Decimal('0') for s in self.servicos.all()), Decimal('0'))

    @property
    def total(self):
        return self.total_produtos + self.total_servicos


class VendaProduto(models.Model):
    """Item de PRODUTO da venda (subconjunto das colunas de vendas_produto)."""

    venda = models.ForeignKey(Venda, on_delete=models.CASCADE, related_name='produtos', verbose_name='Venda')
    preco = models.ForeignKey(
        ItemPreco, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Item da tabela de preços',
    )
    tipo_produto = models.CharField(max_length=120, blank=True, verbose_name='Tipo de Produto')
    categoria = models.CharField(max_length=120, blank=True, verbose_name='Categoria')
    subcategoria = models.CharField(max_length=120, blank=True, verbose_name='Subcategoria')
    nome_produto = models.CharField(max_length=200, verbose_name='Produto')
    marca = models.CharField(max_length=80, blank=True, verbose_name='Marca')
    modelo = models.CharField(max_length=200, blank=True, verbose_name='Modelo')
    sku = models.CharField(max_length=60, blank=True, verbose_name='SKU')
    serial = models.CharField(max_length=120, blank=True, verbose_name='Serial')
    cor = models.CharField(max_length=60, blank=True, verbose_name='Cor')
    qtde = models.PositiveIntegerField(default=1, verbose_name='Qtde')
    custo = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Custo')
    valor_venda = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), verbose_name='Valor de Venda')
    plano = models.CharField(max_length=200, blank=True, verbose_name='Plano')
    tabela_preco = models.CharField(max_length=120, blank=True, verbose_name='Tabela de preço')
    pilar = models.CharField(max_length=40, blank=True, verbose_name='Pilar')

    # SLV: valor sugerido × final, quem mudou, descontos.
    categoria_slv = models.CharField(max_length=12, blank=True, default='', db_default='',
                                     verbose_name='Categoria (aparelho/eletrônico/essencial)')
    prateleira_infinita = models.BooleanField(default=False, db_default=False, verbose_name='Prateleira infinita')
    valor_sugerido = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                         verbose_name='Valor sugerido (tabela)')
    regra_preco = models.CharField(max_length=120, blank=True, default='', db_default='', verbose_name='Regra do preço')
    renova_vini = models.BooleanField(default=False, db_default=False, verbose_name='Renova Vini')
    renova_vini_valor = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'),
                                            db_default=Decimal('0'), verbose_name='Valor Renova Vini')
    renova_codigo = models.CharField(max_length=20, blank=True, default='', db_default='', verbose_name='Código do Renova')
    renova_alied = models.BooleanField(default=False, db_default=False, verbose_name='Renova Alied')
    renova_alied_valor = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'),
                                             db_default=Decimal('0'), verbose_name='Valor Renova Alied')
    desconto_vivo_mais = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'),
                                             db_default=Decimal('0'), verbose_name='Desconto Vivo+')
    valor_calculado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                          verbose_name='Valor calculado pelo sistema')
    valor_editado = models.BooleanField(default=False, db_default=False, verbose_name='Valor final editado à mão')
    editado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Valor editado por')

    class Meta:
        verbose_name = 'Produto da Venda'
        verbose_name_plural = 'Produtos da Venda'

    def __str__(self):
        return self.nome_produto

    @property
    def valor_total(self):
        return (self.valor_venda or Decimal('0')) * (self.qtde or 1)


class VendaServico(models.Model):
    """Item de SERVIÇO da venda (subconjunto das colunas de vendas_servicos)."""

    venda = models.ForeignKey(Venda, on_delete=models.CASCADE, related_name='servicos', verbose_name='Venda')
    preco = models.ForeignKey(
        ItemPreco, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Item da tabela de preços',
    )
    servico = models.CharField(max_length=200, verbose_name='Serviço')
    servico_tecnico = models.CharField(max_length=200, blank=True, verbose_name='Serviço Técnico')
    tipo_plano = models.CharField(max_length=120, blank=True, verbose_name='Tipo do Plano')
    plano_novo = models.CharField(max_length=200, blank=True, verbose_name='Plano')
    grupamento = models.CharField(max_length=120, blank=True, verbose_name='Grupamento')
    numero_acesso = models.CharField(max_length=40, blank=True, verbose_name='Nº de Acesso')
    valor_plano = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), verbose_name='Valor do Plano')
    receita = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Receita')
    status_servico = models.CharField(max_length=60, blank=True, verbose_name='Status do Serviço')
    pilar = models.CharField(max_length=40, blank=True, verbose_name='Pilar')

    # SLV
    TIPOS = [
        ('ALTA', 'Alta'), ('REATIVACAO', 'Reativação'), ('TROCA_PLANO', 'Troca de plano'),
        ('TROCA_SIMCARD', 'Troca de SimCard'), ('MIGRACAO', 'Migração'), ('SEGURO', 'Seguro'), ('SVA', 'SVA'),
        ('TROCA_TITULARIDADE', 'Troca de titularidade'), ('TROCA_NUMERO', 'Troca de número'),
    ]
    tipo_servico = models.CharField(max_length=20, blank=True, default='', db_default='', choices=TIPOS,
                                    db_index=True, verbose_name='Tipo de serviço')
    segmentacao = models.CharField(max_length=10, blank=True, default='', db_default='', verbose_name='Segmentação')
    plano = models.ForeignKey('Plano', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                              verbose_name='Plano novo')
    plano_anterior = models.ForeignKey('Plano', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                       verbose_name='Plano anterior')
    plano_anterior_nome = models.CharField(max_length=200, blank=True, default='', db_default='', verbose_name='Plano anterior (texto)')
    valor_anterior = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Valor anterior')
    delta = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Delta')
    linhas = models.JSONField(default=list, db_default=[], blank=True, verbose_name='Linhas do cliente')
    numero_fake = models.BooleanField(default=False, db_default=False, verbose_name='Número fictício')
    serial_simcard = models.CharField(max_length=30, blank=True, default='', db_default='', verbose_name='Serial do SimCard')
    servico_adicional = models.ForeignKey('ServicoAdicional', on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name='+', verbose_name='Seguro / SVA')
    ativacao_confirmada = models.BooleanField(default=False, db_default=False, verbose_name='Ativação confirmada')
    novo_titular_cpf = models.CharField(max_length=11, blank=True, default='', db_default='', verbose_name='CPF do novo titular')
    novo_titular_nome = models.CharField(max_length=200, blank=True, default='', db_default='', verbose_name='Novo titular')
    numero_anterior = models.CharField(max_length=20, blank=True, default='', db_default='', verbose_name='Número anterior')
    numero_novo = models.CharField(max_length=20, blank=True, default='', db_default='', verbose_name='Número novo')

    class Meta:
        verbose_name = 'Serviço da Venda'
        verbose_name_plural = 'Serviços da Venda'

    def __str__(self):
        return self.servico



# ═══════════════════════════════════════════════════════════════════════════
# SLV — Sistema de Lançamento de Vendas (Documento de Requisitos v1.0)
# ═══════════════════════════════════════════════════════════════════════════
SEGMENTACOES = [('POS', 'Pós'), ('CONTROLE', 'Controle'), ('PRE', 'Pré'), ('EMPRESAS', 'Vivo Empresas')]


class Plano(models.Model):
    """[Parametrização.RF001] Planos, segmentações e valores — o administrador mantém."""

    nome = models.CharField(max_length=200, verbose_name='Nome do plano')
    segmentacao = models.CharField(max_length=10, choices=SEGMENTACOES, db_index=True, verbose_name='Segmentação')
    valor = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor mensal (R$)')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Plano (SLV)'
        verbose_name_plural = 'Planos (SLV)'
        ordering = ['segmentacao', 'valor', 'nome']

    def __str__(self):
        return f'{self.get_segmentacao_display()} — {self.nome}'


class ConfiguracaoVendas(models.Model):
    """[Parametrização.RF002] Percentual do Vivo+ (linha única)."""

    vivo_mais_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'),
                                               verbose_name='Desconto Vivo+ (%)')
    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')

    class Meta:
        verbose_name = 'Configuração de vendas'
        verbose_name_plural = 'Configuração de vendas'

    @classmethod
    def atual(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


class ServicoAdicional(models.Model):
    """[Venda de serviços.RF008] Lista de seguros e SVAs disponíveis."""

    TIPOS = [('SEGURO', 'Seguro'), ('SVA', 'SVA')]
    tipo = models.CharField(max_length=10, choices=TIPOS, verbose_name='Tipo')
    nome = models.CharField(max_length=200, verbose_name='Nome')
    valor = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), verbose_name='Valor (R$)')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')

    class Meta:
        verbose_name = 'Seguro / SVA'
        verbose_name_plural = 'Seguros e SVAs'
        ordering = ['tipo', 'nome']

    def __str__(self):
        return f'{self.get_tipo_display()} — {self.nome}'


class Cliente(models.Model):
    """[Início da venda.RF002/RF003] Cliente identificado pelo CPF (11 dígitos)."""

    cpf = models.CharField(max_length=11, unique=True, verbose_name='CPF')
    nome = models.CharField(max_length=200, verbose_name='Nome')
    telefone = models.CharField(max_length=20, blank=True, verbose_name='Telefone')
    cep = models.CharField(max_length=9, blank=True, verbose_name='CEP')
    logradouro = models.CharField(max_length=200, blank=True, verbose_name='Logradouro')
    numero = models.CharField(max_length=20, blank=True, verbose_name='Número')
    complemento = models.CharField(max_length=100, blank=True, verbose_name='Complemento')
    bairro = models.CharField(max_length=100, blank=True, verbose_name='Bairro')
    cidade = models.CharField(max_length=100, blank=True, verbose_name='Cidade')
    uf = models.CharField(max_length=2, blank=True, verbose_name='UF')
    # Plano atual (o último lançado num serviço) — alimenta a pré-análise e o delta.
    plano = models.ForeignKey(Plano, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                              verbose_name='Plano atual')
    plano_nome = models.CharField(max_length=200, blank=True, verbose_name='Plano atual (texto)')
    segmentacao = models.CharField(max_length=10, blank=True, verbose_name='Segmentação')
    valor_pago = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Valor pago')
    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cliente (SLV)'
        verbose_name_plural = 'Clientes (SLV)'
        ordering = ['nome']

    def __str__(self):
        return f'{self.nome} ({self.cpf_formatado})'

    @property
    def cpf_formatado(self):
        d = self.cpf
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}' if len(d) == 11 else d

    @property
    def endereco(self):
        partes = [self.logradouro, self.numero, self.complemento, self.bairro,
                  f'{self.cidade}/{self.uf}' if self.cidade else self.uf]
        return ', '.join(p for p in partes if p)


class RegistroAlteracao(models.Model):
    """[Confiabilidade.NF001] Histórico: data, usuário, PDV, valor anterior e novo."""

    TIPOS = [('PLANO', 'Plano'), ('VIVO_MAIS', 'Percentual Vivo+'), ('CLIENTE', 'Cliente'),
             ('SERVICO_ADICIONAL', 'Seguro / SVA')]
    tipo = models.CharField(max_length=20, choices=TIPOS, db_index=True, verbose_name='O que mudou')
    objeto_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    objeto_rotulo = models.CharField(max_length=200, blank=True, verbose_name='Registro')
    campo = models.CharField(max_length=40, verbose_name='Campo')
    rotulo = models.CharField(max_length=80, verbose_name='Campo (rótulo)')
    antes = models.TextField(blank=True, verbose_name='Valor anterior')
    depois = models.TextField(blank=True, verbose_name='Valor novo')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    usuario_nome = models.CharField(max_length=200, blank=True)
    pdv = models.CharField(max_length=120, blank=True, verbose_name='PDV')
    quando = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Histórico de alteração (SLV)'
        verbose_name_plural = 'Histórico de alterações (SLV)'
        ordering = ['-quando', '-id']


class ImportacaoPrecos(models.Model):
    """[Parametrização.RF003] Relatório de cada importação da tabela de produtos."""

    quando = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    origem = models.CharField(max_length=10, default='manual', choices=[('manual', 'Envio manual'), ('email', 'E-mail')])
    arquivo = models.CharField(max_length=255, blank=True)
    incluidos = models.PositiveIntegerField(default=0)
    alterados = models.PositiveIntegerField(default=0)
    sem_mudanca = models.PositiveIntegerField(default=0)
    rejeitados = models.JSONField(default=list, blank=True)
    erro = models.TextField(blank=True, help_text='Se a importação falhou, a tabela vigente ficou como estava.')

    class Meta:
        verbose_name = 'Importação da tabela de produtos'
        verbose_name_plural = 'Importações da tabela de produtos'
        ordering = ['-quando']
