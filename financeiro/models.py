"""Financeiro do portal: títulos a pagar e a receber, baixas, contas bancárias,
conciliações (bancária e de cartões), plano de contas e a trilha de auditoria.

Como o dinheiro anda aqui:

- **Título** é a obrigação: um boleto a pagar, um aluguel, um repasse a receber.
  Nasce em aberto, com vencimento e valor; pode vir em parcelas (mesmo
  ``grupo``).
- **Baixa** é o pagamento (ou recebimento) de um título, numa conta bancária e
  numa data. Um título pode ter várias baixas (pagamento parcial). A baixa nunca
  é apagada: "cancelar" uma conta paga é **estornar** a baixa, com motivo — o
  título volta a ficar em aberto e o histórico fica.
- **Movimento** é o que passa na conta bancária: cada baixa gera um, e ajustes
  (tarifa, rendimento, transferência) entram direto. É contra os movimentos
  que o extrato do banco é conciliado.
- **Log** guarda quem fez o quê, quando, de onde e o antes/depois de cada
  mudança — nada no módulo muda sem passar por ele.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

ZERO = Decimal('0.00')


def _storage_de_midia():
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage()
    return None


def caminho_anexo_titulo(instancia, nome):
    return f'financeiro/titulos/{timezone.now():%Y/%m}/{uuid.uuid4().hex}_{nome[-80:]}'


def caminho_comprovante_baixa(instancia, nome):
    return f'financeiro/baixas/{timezone.now():%Y/%m}/{uuid.uuid4().hex}_{nome[-80:]}'


def _dinheiro(**kw):
    return models.DecimalField(max_digits=14, decimal_places=2, **kw)


# ── Acesso ──────────────────────────────────────────────────────────────────
class AcessoFinanceiro(models.Model):
    """O que o SUPERADMIN liberou para uma pessoa dentro do Financeiro.

    ``permissoes`` é a lista de chaves "área.ação" (ver ``permissoes.AREAS``).
    ``lojas`` recorta o que ela enxerga: vazio = todas as lojas.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='acesso_financeiro', verbose_name='Pessoa')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')
    permissoes = models.JSONField(default=list, blank=True, verbose_name='Permissões')
    lojas = models.ManyToManyField('users.Sector', blank=True, related_name='+',
                                   verbose_name='Lojas (vazio = todas)')
    observacao = models.CharField(max_length=200, blank=True, verbose_name='Observação')
    concedido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                      blank=True, related_name='+', verbose_name='Liberado por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Acesso ao Financeiro'
        verbose_name_plural = 'Acessos ao Financeiro'

    def __str__(self):
        return f'{self.user} · {len(self.permissoes or [])} permissão(ões)'


# ── Cadastros ───────────────────────────────────────────────────────────────
class PlanoConta(models.Model):
    """Conta do plano de contas. Só as analíticas recebem lançamento."""

    TIPOS = [('RECEITA', 'Receita'), ('DESPESA', 'Despesa')]

    codigo = models.CharField(max_length=20, unique=True, verbose_name='Código')
    nome = models.CharField(max_length=120, verbose_name='Nome')
    tipo = models.CharField(max_length=7, choices=TIPOS, verbose_name='Tipo')
    pai = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                            related_name='filhas', verbose_name='Conta superior')
    analitica = models.BooleanField(default=True, verbose_name='Recebe lançamento')
    ativa = models.BooleanField(default=True, verbose_name='Ativa')
    descricao = models.CharField(max_length=255, blank=True, verbose_name='Descrição')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'Conta do plano'
        verbose_name_plural = 'Plano de contas'

    def __str__(self):
        return f'{self.codigo} {self.nome}'

    @property
    def nivel(self):
        return self.codigo.count('.')


class ContaBancaria(models.Model):
    TIPOS = [
        ('CORRENTE', 'Conta corrente'),
        ('POUPANCA', 'Poupança'),
        ('APLICACAO', 'Aplicação'),
        ('CAIXA', 'Caixa (dinheiro)'),
        ('PAGAMENTO', 'Conta de pagamento / carteira digital'),
    ]

    nome = models.CharField(max_length=80, verbose_name='Apelido')
    tipo = models.CharField(max_length=10, choices=TIPOS, default='CORRENTE', verbose_name='Tipo')
    banco_codigo = models.CharField(max_length=5, blank=True, verbose_name='Código do banco')
    banco_nome = models.CharField(max_length=80, blank=True, verbose_name='Banco')
    agencia = models.CharField(max_length=10, blank=True, verbose_name='Agência')
    conta = models.CharField(max_length=20, blank=True, verbose_name='Conta')
    digito = models.CharField(max_length=2, blank=True, verbose_name='Dígito')
    titular_documento = models.CharField(max_length=14, blank=True, verbose_name='CNPJ/CPF do titular')
    titular_nome = models.CharField(max_length=150, blank=True, verbose_name='Titular')
    loja = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Loja')
    chave_pix = models.CharField(max_length=120, blank=True, verbose_name='Chave PIX')
    saldo_inicial = _dinheiro(default=ZERO, verbose_name='Saldo inicial')
    data_saldo_inicial = models.DateField(default=timezone.localdate, verbose_name='Saldo inicial em')
    ativa = models.BooleanField(default=True, verbose_name='Ativa')
    observacao = models.TextField(blank=True, verbose_name='Observação')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-ativa', 'nome']
        verbose_name = 'Conta bancária'
        verbose_name_plural = 'Contas bancárias'

    def __str__(self):
        return self.nome

    @property
    def descricao_bancaria(self):
        partes = []
        if self.banco_codigo or self.banco_nome:
            partes.append(' '.join(x for x in (self.banco_codigo, self.banco_nome) if x))
        if self.agencia:
            partes.append(f'Ag. {self.agencia}')
        if self.conta:
            partes.append(f'C/C {self.conta}' + (f'-{self.digito}' if self.digito else ''))
        return ' · '.join(partes)

    def saldo(self, ate=None):
        """Saldo na data (inclusive): inicial + movimentos não estornados a partir da data inicial."""
        qs = self.movimentos.filter(estornado=False, data__gte=self.data_saldo_inicial)
        if ate:
            qs = qs.filter(data__lte=ate)
        return self.saldo_inicial + (qs.aggregate(s=models.Sum('valor'))['s'] or ZERO)


# ── Títulos ─────────────────────────────────────────────────────────────────
class Titulo(models.Model):
    TIPOS = [('PAGAR', 'A pagar'), ('RECEBER', 'A receber')]
    STATUS = [('ABERTO', 'Em aberto'), ('PARCIAL', 'Parcial'), ('QUITADO', 'Quitado'), ('CANCELADO', 'Cancelado')]
    FORMAS = [
        ('BOLETO', 'Boleto'), ('PIX', 'PIX'), ('TED', 'TED/DOC'), ('DINHEIRO', 'Dinheiro'),
        ('CARTAO', 'Cartão'), ('DEBITO', 'Débito automático'), ('CHEQUE', 'Cheque'), ('OUTRO', 'Outro'),
    ]
    ORIGENS = [('MANUAL', 'Manual'), ('NFE', 'Nota fiscal (SEFAZ)'), ('PISCOFINS', 'PIS/Cofins'),
               ('IMPORTACAO', 'Importação')]

    tipo = models.CharField(max_length=7, choices=TIPOS, db_index=True, verbose_name='Tipo')
    descricao = models.CharField(max_length=200, verbose_name='Descrição')
    pessoa_nome = models.CharField(max_length=200, blank=True, verbose_name='Fornecedor / cliente')
    pessoa_documento = models.CharField(max_length=14, blank=True, db_index=True, verbose_name='CNPJ/CPF')
    numero_documento = models.CharField(max_length=60, blank=True, verbose_name='Nº do documento')
    plano = models.ForeignKey(PlanoConta, on_delete=models.PROTECT, null=True, blank=True,
                              related_name='titulos', verbose_name='Plano de contas')
    loja = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Loja')
    conta_prevista = models.ForeignKey(ContaBancaria, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', verbose_name='Conta prevista')
    forma = models.CharField(max_length=10, choices=FORMAS, blank=True, verbose_name='Forma prevista')
    codigo_barras = models.CharField(max_length=60, blank=True, verbose_name='Linha digitável')
    pix_favorecido = models.CharField(max_length=120, blank=True, verbose_name='Chave PIX do favorecido')

    emissao = models.DateField(default=timezone.localdate, verbose_name='Emissão')
    vencimento = models.DateField(db_index=True, verbose_name='Vencimento')
    competencia = models.DateField(null=True, blank=True, verbose_name='Competência')
    valor = _dinheiro(verbose_name='Valor')
    valor_baixado = _dinheiro(default=ZERO, verbose_name='Valor já baixado')
    parcela = models.PositiveSmallIntegerField(default=1, verbose_name='Parcela')
    parcelas = models.PositiveSmallIntegerField(default=1, verbose_name='De')
    grupo = models.UUIDField(default=uuid.uuid4, db_index=True, verbose_name='Grupo das parcelas')

    status = models.CharField(max_length=9, choices=STATUS, default='ABERTO', db_index=True, verbose_name='Situação')
    observacao = models.TextField(blank=True, verbose_name='Observação')
    anexo = models.FileField(upload_to=caminho_anexo_titulo, storage=_storage_de_midia(), null=True, blank=True,
                             verbose_name='Anexo')
    anexo_nome = models.CharField(max_length=255, blank=True, verbose_name='Nome do anexo')
    origem = models.CharField(max_length=10, choices=ORIGENS, default='MANUAL', verbose_name='Origem')
    nota = models.ForeignKey('contagem_caixa.NotaRecebida', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='titulos', verbose_name='Nota fiscal')

    cancelado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                      blank=True, related_name='+', verbose_name='Cancelado por')
    cancelado_em = models.DateTimeField(null=True, blank=True, verbose_name='Cancelado em')
    motivo_cancelamento = models.CharField(max_length=255, blank=True, verbose_name='Motivo do cancelamento')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='Criado por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['vencimento', 'id']
        verbose_name = 'Título'
        verbose_name_plural = 'Títulos'
        indexes = [models.Index(fields=['tipo', 'status', 'vencimento'])]

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.descricao} · {self.vencimento:%d/%m/%Y}'

    @property
    def saldo(self):
        if self.status == 'CANCELADO':
            return ZERO
        return max(self.valor - self.valor_baixado, ZERO)

    @property
    def vencido(self):
        return self.status in ('ABERTO', 'PARCIAL') and self.vencimento < timezone.localdate()

    @property
    def rotulo_parcela(self):
        return f'{self.parcela}/{self.parcelas}' if self.parcelas > 1 else ''


class Baixa(models.Model):
    """Um pagamento (ou recebimento) de um título. Não se apaga: estorna-se."""

    titulo = models.ForeignKey(Titulo, on_delete=models.PROTECT, related_name='baixas', verbose_name='Título')
    data = models.DateField(db_index=True, verbose_name='Data do pagamento')
    valor = _dinheiro(verbose_name='Valor principal')
    juros = _dinheiro(default=ZERO, verbose_name='Juros')
    multa = _dinheiro(default=ZERO, verbose_name='Multa')
    desconto = _dinheiro(default=ZERO, verbose_name='Desconto')
    conta = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT, related_name='baixas',
                              verbose_name='Conta')
    forma = models.CharField(max_length=10, choices=Titulo.FORMAS, verbose_name='Forma')
    observacao = models.CharField(max_length=255, blank=True, verbose_name='Observação')
    comprovante = models.FileField(upload_to=caminho_comprovante_baixa, storage=_storage_de_midia(),
                                   null=True, blank=True, verbose_name='Comprovante')
    comprovante_nome = models.CharField(max_length=255, blank=True)

    estornada = models.BooleanField(default=False, db_index=True, verbose_name='Estornada')
    estornada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                      blank=True, related_name='+', verbose_name='Estornada por')
    estornada_em = models.DateTimeField(null=True, blank=True, verbose_name='Estornada em')
    motivo_estorno = models.CharField(max_length=255, blank=True, verbose_name='Motivo do estorno')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='Baixado por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-data', '-id']
        verbose_name = 'Baixa'
        verbose_name_plural = 'Baixas'

    def __str__(self):
        return f'Baixa de {self.titulo} em {self.data:%d/%m/%Y}'

    @property
    def total(self):
        """O que de fato saiu/entrou na conta."""
        return self.valor + self.juros + self.multa - self.desconto


# ── Banco ───────────────────────────────────────────────────────────────────
class MovimentoBancario(models.Model):
    """O que passa na conta: + entra, − sai."""

    ORIGENS = [('BAIXA', 'Baixa de título'), ('AJUSTE', 'Lançamento avulso'),
               ('TRANSFERENCIA', 'Transferência entre contas')]

    conta = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT, related_name='movimentos',
                              verbose_name='Conta')
    data = models.DateField(db_index=True, verbose_name='Data')
    valor = _dinheiro(verbose_name='Valor (+ entrada / − saída)')
    descricao = models.CharField(max_length=200, verbose_name='Descrição')
    origem = models.CharField(max_length=13, choices=ORIGENS, default='AJUSTE', verbose_name='Origem')
    baixa = models.OneToOneField(Baixa, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='movimento', verbose_name='Baixa')
    plano = models.ForeignKey(PlanoConta, on_delete=models.PROTECT, null=True, blank=True,
                              related_name='movimentos', verbose_name='Plano de contas')
    transferencia = models.UUIDField(null=True, blank=True, db_index=True,
                                     verbose_name='Par da transferência')
    estornado = models.BooleanField(default=False, db_index=True, verbose_name='Estornado')
    conciliado = models.BooleanField(default=False, db_index=True, verbose_name='Conciliado')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='Lançado por')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-data', '-id']
        verbose_name = 'Movimento bancário'
        verbose_name_plural = 'Movimentos bancários'

    def __str__(self):
        return f'{self.conta} · {self.data:%d/%m/%Y} · {self.valor}'


class ExtratoImportacao(models.Model):
    conta = models.ForeignKey(ContaBancaria, on_delete=models.CASCADE, related_name='importacoes')
    arquivo_nome = models.CharField(max_length=255, verbose_name='Arquivo')
    formato = models.CharField(max_length=4, verbose_name='Formato')
    inicio = models.DateField(null=True, blank=True)
    fim = models.DateField(null=True, blank=True)
    linhas_novas = models.PositiveIntegerField(default=0)
    linhas_repetidas = models.PositiveIntegerField(default=0)
    importado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                      blank=True, related_name='+')
    importado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-importado_em']


class ExtratoLinha(models.Model):
    """Uma linha do extrato do banco, à espera de casar com um movimento."""

    conta = models.ForeignKey(ContaBancaria, on_delete=models.CASCADE, related_name='extrato')
    importacao = models.ForeignKey(ExtratoImportacao, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='linhas')
    data = models.DateField(db_index=True)
    valor = _dinheiro()
    descricao = models.CharField(max_length=255, blank=True)
    documento = models.CharField(max_length=80, blank=True, verbose_name='Documento / FITID')
    identificador = models.CharField(max_length=120, verbose_name='Identificador único')
    movimento = models.OneToOneField(MovimentoBancario, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='extrato_linha')
    ignorada = models.BooleanField(default=False)
    conciliada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       blank=True, related_name='+')
    conciliada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-data', 'id']
        constraints = [models.UniqueConstraint(fields=['conta', 'identificador'], name='fin_extrato_linha_unica')]

    @property
    def conciliada(self):
        return self.movimento_id is not None


# ── Cartões ─────────────────────────────────────────────────────────────────
class EstabelecimentoCartao(models.Model):
    """Código do estabelecimento na adquirente (EC) → loja."""

    codigo = models.CharField(max_length=40, unique=True, verbose_name='Código do estabelecimento')
    adquirente = models.CharField(max_length=40, blank=True, verbose_name='Adquirente')
    loja = models.ForeignKey('users.Sector', on_delete=models.CASCADE, related_name='+', verbose_name='Loja')

    class Meta:
        ordering = ['codigo']


class RecebivelCartao(models.Model):
    """Uma venda no cartão segundo a adquirente (Cielo, Rede, Stone, Getnet…)."""

    STATUS = [('PENDENTE', 'Sem par'), ('CONCILIADO', 'Conciliado'), ('DIVERGENTE', 'Valor diferente'),
              ('IGNORADO', 'Ignorado')]

    adquirente = models.CharField(max_length=40, blank=True, verbose_name='Adquirente')
    estabelecimento = models.CharField(max_length=40, blank=True, db_index=True, verbose_name='Estabelecimento')
    loja = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Loja')
    data_venda = models.DateField(db_index=True, verbose_name='Data da venda')
    nsu = models.CharField(max_length=40, db_index=True, verbose_name='NSU')
    autorizacao = models.CharField(max_length=40, blank=True, verbose_name='Autorização')
    bandeira = models.CharField(max_length=40, blank=True, verbose_name='Bandeira')
    modalidade = models.CharField(max_length=40, blank=True, verbose_name='Modalidade')
    parcelas = models.PositiveSmallIntegerField(default=1, verbose_name='Parcelas')
    valor_bruto = _dinheiro(verbose_name='Valor bruto')
    taxa = _dinheiro(default=ZERO, verbose_name='Taxa')
    valor_liquido = _dinheiro(default=ZERO, verbose_name='Valor líquido')
    previsao_pagamento = models.DateField(null=True, blank=True, verbose_name='Previsão de pagamento')
    identificador = models.CharField(max_length=160, unique=True, verbose_name='Identificador único')

    venda = models.ForeignKey('vendas.Venda', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='recebiveis_cartao', verbose_name='Venda no portal')
    status = models.CharField(max_length=10, choices=STATUS, default='PENDENTE', db_index=True)
    observacao = models.CharField(max_length=255, blank=True)
    conciliado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       blank=True, related_name='+')
    conciliado_em = models.DateTimeField(null=True, blank=True)
    importado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                      blank=True, related_name='+')
    importado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-data_venda', 'id']


# ── Rastreabilidade ─────────────────────────────────────────────────────────
class LogFinanceiro(models.Model):
    """Quem fez, o quê, quando e de onde — com o antes e o depois."""

    ACOES = [
        ('CRIAR', 'Criou'), ('EDITAR', 'Editou'), ('EXCLUIR', 'Excluiu'), ('CANCELAR', 'Cancelou'),
        ('BAIXAR', 'Baixou'), ('ESTORNAR', 'Estornou'), ('EDITAR_BAIXA', 'Editou a baixa'),
        ('IMPORTAR', 'Importou'), ('CONCILIAR', 'Conciliou'), ('DESCONCILIAR', 'Desfez a conciliação'),
        ('ACESSO', 'Mudou acessos'), ('EXPORTAR', 'Exportou'), ('MANIFESTAR', 'Manifestou nota'),
    ]

    quando = models.DateTimeField(default=timezone.now, db_index=True, verbose_name='Quando')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+', verbose_name='Usuário')
    usuario_nome = models.CharField(max_length=200, blank=True, verbose_name='Nome na hora')
    usuario_setor = models.CharField(max_length=120, blank=True, verbose_name='Setor na hora')
    acao = models.CharField(max_length=12, choices=ACOES, db_index=True, verbose_name='Ação')
    area = models.CharField(max_length=20, blank=True, db_index=True, verbose_name='Área')
    objeto_tipo = models.CharField(max_length=40, blank=True, db_index=True, verbose_name='Tipo')
    objeto_id = models.CharField(max_length=40, blank=True, db_index=True, verbose_name='ID')
    descricao = models.CharField(max_length=300, verbose_name='O que aconteceu')
    valor = _dinheiro(null=True, blank=True, verbose_name='Valor')
    loja = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Loja do movimento')
    antes = models.JSONField(null=True, blank=True, verbose_name='Antes')
    depois = models.JSONField(null=True, blank=True, verbose_name='Depois')
    ip = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP')
    dispositivo = models.CharField(max_length=255, blank=True, verbose_name='Navegador / dispositivo')
    rota = models.CharField(max_length=255, blank=True, verbose_name='Tela')

    class Meta:
        ordering = ['-quando', '-id']
        verbose_name = 'Registro de auditoria'
        verbose_name_plural = 'Auditoria do Financeiro'
        indexes = [models.Index(fields=['objeto_tipo', 'objeto_id'])]

    def __str__(self):
        return f'{self.quando:%d/%m/%Y %H:%M} · {self.usuario_nome} · {self.descricao}'
