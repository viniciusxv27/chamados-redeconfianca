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
    # Soma das sangrias registradas na aba Sangrias (model `Sangria`) para esta
    # loja e dia. Não se digita: é regravada a cada sangria criada, alterada ou
    # apagada. O "sangria/erro" acima continua manual, para erro e ajuste.
    sangria_registrada = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO, db_default=ZERO,
        verbose_name='Sangrias registradas')
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
                - (self.sangria_registrada or ZERO)
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
        """Saldo do dia = saldo anterior + Valor real + Diferença − Depósito.

        Valor real + Diferença é a Entrada — tudo o que entrou na gaveta no
        dia. Somando só o Valor real, o saldo ficava parado enquanto a loja não
        contasse o dia (e hoje quase nenhuma conta), e a diferença, que é
        dinheiro que a loja continua devendo, nunca chegava no saldo. Assim o
        saldo é sempre o que tem de haver na gaveta, contado ou não.

        O que foi depositado saiu da gaveta e foi para o banco; continuar
        somando no saldo faria o caixa parecer ter dinheiro que não tem.
        """
        return ((saldo_anterior or ZERO)
                + (self.valor_real or ZERO)
                + (self.diferenca or ZERO)
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


# ── Sangrias ────────────────────────────────────────────────────────────────
def caminho_comprovante_sangria(instance, filename):
    import os
    import uuid
    ext = os.path.splitext(filename)[1].lower() or '.jpg'
    return f'contagem_caixa/sangrias/{instance.data:%Y/%m}/{uuid.uuid4().hex}{ext}'


def _storage_de_midia():
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage()
    return None


class CategoriaSangria(models.Model):
    """Para que o dinheiro saiu da gaveta. A lista é do gestor."""

    nome = models.CharField(max_length=60, unique=True, verbose_name='Categoria')
    ativa = models.BooleanField(default=True, verbose_name='Ativa')
    ordem = models.PositiveSmallIntegerField(default=0, verbose_name='Ordem')

    class Meta:
        verbose_name = 'Categoria de sangria'
        verbose_name_plural = 'Categorias de sangria'
        ordering = ['ordem', 'nome']

    def __str__(self):
        return self.nome


class Sangria(models.Model):
    """Dinheiro retirado do caixa da loja, com o motivo.

    Cada registro entra sozinho no dia de caixa da loja
    (``ContagemCaixaDia.sangria_registrada``) e, por ele, na Entrada e no saldo
    — sem planilha paralela. A conferência é do gestor: marca que viu o
    comprovante e o valor bate.
    """

    loja = models.ForeignKey(
        Sector, on_delete=models.CASCADE, related_name='sangrias', verbose_name='Loja')
    data = models.DateField(db_index=True, verbose_name='Data da sangria')
    valor = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor')
    categoria = models.ForeignKey(
        CategoriaSangria, on_delete=models.PROTECT, related_name='sangrias', verbose_name='Categoria')
    descricao = models.TextField(verbose_name='Descrição')
    favorecido = models.CharField(
        max_length=120, blank=True, verbose_name='Pago a',
        help_text='Quem recebeu o dinheiro (fornecedor, pessoa, estabelecimento).')
    comprovante = models.FileField(
        upload_to=caminho_comprovante_sangria, storage=_storage_de_midia(),
        null=True, blank=True, verbose_name='Comprovante')

    registrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sangrias_registradas', verbose_name='Registrada por')
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    conferida = models.BooleanField(default=False, db_index=True, verbose_name='Conferida')
    conferida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sangrias_conferidas', verbose_name='Conferida por')
    conferida_em = models.DateTimeField(null=True, blank=True, verbose_name='Conferida em')
    observacao_conferencia = models.CharField(
        max_length=255, blank=True, verbose_name='Observação da conferência')

    class Meta:
        verbose_name = 'Sangria'
        verbose_name_plural = 'Sangrias'
        ordering = ['-data', '-criada_em']
        indexes = [models.Index(fields=['loja', 'data'])]

    def __str__(self):
        return f'{self.loja.name} — {self.data:%d/%m/%Y} — R$ {self.valor}'


# ── Contas a pagar: PIS/Cofins ──────────────────────────────────────────────
def caminho_documento_piscofins(instance, filename):
    import os
    import uuid
    ext = os.path.splitext(filename)[1].lower() or '.pdf'
    return f'contagem_caixa/piscofins/{instance.competencia:%Y/%m}/{uuid.uuid4().hex}{ext}'


class FornecedorPisCofins(models.Model):
    """O fornecedor pelo CNPJ, com o cadastro trazido da Receita."""

    cnpj = models.CharField(max_length=14, unique=True, verbose_name='CNPJ')
    razao_social = models.CharField(max_length=200, verbose_name='Razão social')
    nome_fantasia = models.CharField(max_length=200, blank=True, verbose_name='Nome fantasia')
    situacao = models.CharField(max_length=40, blank=True, verbose_name='Situação cadastral')
    atividade = models.CharField(max_length=255, blank=True, verbose_name='Atividade principal')
    simples = models.BooleanField(null=True, blank=True, verbose_name='Optante pelo Simples')
    municipio = models.CharField(max_length=80, blank=True, verbose_name='Município')
    uf = models.CharField(max_length=2, blank=True, verbose_name='UF')
    consultado_em = models.DateTimeField(null=True, blank=True, verbose_name='Consultado na Receita em')

    class Meta:
        verbose_name = 'Fornecedor (PIS/Cofins)'
        verbose_name_plural = 'Fornecedores (PIS/Cofins)'
        ordering = ['razao_social']

    def __str__(self):
        return f'{self.razao_social} ({self.cnpj_formatado})'

    @property
    def cnpj_formatado(self):
        from .cnpj import formatar
        return formatar(self.cnpj)

    @property
    def link_receita(self):
        from .cnpj import LINK_RECEITA
        return LINK_RECEITA.format(cnpj=self.cnpj)

    @property
    def situacao_ok(self):
        return not self.situacao or self.situacao == 'ATIVA'


class DocumentoPisCofins(models.Model):
    """Nota, boleto ou recibo de aluguel lançado para a apuração do mês."""

    TIPOS = [
        ('NF', 'Nota fiscal'),
        ('BOLETO', 'Boleto'),
        ('ALUGUEL', 'Recibo de aluguel'),
        ('OUTRO', 'Outro'),
    ]

    tipo = models.CharField(max_length=10, choices=TIPOS, verbose_name='Tipo')
    competencia = models.DateField(db_index=True, verbose_name='Competência',
                                   help_text='Sempre o dia 1º do mês de competência.')
    fornecedor = models.ForeignKey(FornecedorPisCofins, on_delete=models.PROTECT,
                                   related_name='documentos', verbose_name='Fornecedor')
    valor = models.DecimalField(max_digits=14, decimal_places=2, verbose_name='Valor')
    numero = models.CharField(max_length=60, blank=True, verbose_name='Número do documento')
    data_documento = models.DateField(null=True, blank=True, verbose_name='Data de emissão')
    loja = models.ForeignKey(Sector, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='documentos_piscofins', verbose_name='Loja / unidade')
    descricao = models.CharField(max_length=255, blank=True, verbose_name='Descrição')
    arquivo = models.FileField(upload_to=caminho_documento_piscofins, storage=_storage_de_midia(),
                               verbose_name='Arquivo')
    nome_arquivo = models.CharField(max_length=255, blank=True, verbose_name='Nome original do arquivo')

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documentos_piscofins', verbose_name='Registrado por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Documento de PIS/Cofins'
        verbose_name_plural = 'Documentos de PIS/Cofins'
        ordering = ['-competencia', '-criado_em']

    def __str__(self):
        return f'{self.get_tipo_display()} {self.numero or ""} — {self.fornecedor.razao_social} — R$ {self.valor}'


# ── Notas emitidas contra o CNPJ (SEFAZ, NF-e Distribuição DFe) ─────────────
class NotaRecebida(models.Model):
    """NF-e que um fornecedor emitiu para um CNPJ da empresa, vinda da SEFAZ."""

    SITUACOES = [('AUTORIZADA', 'Autorizada'), ('CANCELADA', 'Cancelada'), ('DENEGADA', 'Denegada')]

    chave = models.CharField(max_length=44, unique=True, verbose_name='Chave de acesso')
    cnpj_destinatario = models.CharField(max_length=14, db_index=True, verbose_name='CNPJ da empresa')
    emitente_cnpj = models.CharField(max_length=14, db_index=True, verbose_name='CNPJ do emitente')
    emitente_nome = models.CharField(max_length=200, blank=True, verbose_name='Emitente')
    emitente_ie = models.CharField(max_length=20, blank=True, verbose_name='IE do emitente')
    modelo = models.CharField(max_length=2, blank=True, verbose_name='Modelo')
    serie = models.CharField(max_length=3, blank=True, verbose_name='Série')
    numero = models.CharField(max_length=9, blank=True, db_index=True, verbose_name='Número')
    emissao = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name='Emissão')
    valor = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name='Valor')
    situacao = models.CharField(max_length=12, choices=SITUACOES, default='AUTORIZADA', verbose_name='Situação')
    tipo_operacao = models.CharField(max_length=1, blank=True, verbose_name='Tipo (0 entrada, 1 saída)')
    nsu = models.BigIntegerField(default=0, verbose_name='NSU')
    xml_resumo = models.TextField(blank=True, verbose_name='XML do resumo')
    xml_completo = models.TextField(blank=True, verbose_name='XML completo')
    documento = models.ForeignKey('DocumentoPisCofins', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='notas_sefaz', verbose_name='Lançado como')
    ignorada = models.BooleanField(default=False, verbose_name='Fora do PIS/Cofins')
    ignorada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='Marcada por')
    recebida_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Nota recebida (SEFAZ)'
        verbose_name_plural = 'Notas recebidas (SEFAZ)'
        ordering = ['-emissao']

    def __str__(self):
        return f'NF {self.numero} — {self.emitente_nome} — R$ {self.valor}'

    @property
    def emitente_cnpj_formatado(self):
        from .cnpj import formatar
        return formatar(self.emitente_cnpj)

    @property
    def chave_formatada(self):
        return ' '.join(self.chave[i:i + 4] for i in range(0, len(self.chave), 4))


class SincronizacaoDFe(models.Model):
    """Onde parou a leitura da SEFAZ para cada CNPJ (o último NSU)."""

    cnpj = models.CharField(max_length=14, unique=True, verbose_name='CNPJ')
    ult_nsu = models.BigIntegerField(default=0, verbose_name='Último NSU')
    max_nsu = models.BigIntegerField(default=0, verbose_name='Maior NSU na SEFAZ')
    ultima_consulta = models.DateTimeField(null=True, blank=True, verbose_name='Última consulta')
    proxima_consulta = models.DateTimeField(null=True, blank=True, verbose_name='Próxima consulta permitida')
    ultimo_cstat = models.CharField(max_length=10, blank=True, verbose_name='Último código')
    ultima_mensagem = models.CharField(max_length=255, blank=True, verbose_name='Última mensagem')
    notas_novas = models.PositiveIntegerField(default=0, verbose_name='Notas novas na última consulta')

    class Meta:
        verbose_name = 'Sincronização com a SEFAZ'
        verbose_name_plural = 'Sincronizações com a SEFAZ'

    def __str__(self):
        return f'{self.cnpj} — NSU {self.ult_nsu}/{self.max_nsu}'
