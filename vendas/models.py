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

    class Meta:
        verbose_name = 'Serviço da Venda'
        verbose_name_plural = 'Serviços da Venda'

    def __str__(self):
        return self.servico
