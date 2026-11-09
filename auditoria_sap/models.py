"""Visão SAP: espelho local da auditoria SAP × Vivo go.

A fonte é a view ``vw_auditoria_visao_geral`` do MySQL do SAP, que não tem
chave e leva ~30 s para ser lida inteira. Por isso o portal guarda um espelho:
a tela filtra, ordena e pagina em cima do Postgres (rápido), e a leitura do
MySQL acontece só quando alguém manda atualizar.

Cada linha ganha uma ``chave`` — o sha1 das colunas que identificam a
divergência (tipo do erro, venda, documento, produto, serial, ordem, nota e os
dois valores). Medido em 24/09/2026: 4.022 linhas, 4.022 chaves distintas. É
essa chave que segura o "resolvida", que é do portal e não do SAP.
"""
import hashlib
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

# As colunas que dizem *qual* divergência é esta. Mudou alguma delas, é outra
# linha — e é isso que se quer: a auditoria mudou, a marcação não vale mais.
COLUNAS_IDENTIDADE = (
    'TIPO_ERRO', 'DATA_VENDA', 'PDV', 'ID_VENDA', 'DOCUMENTO_VIVOGO', 'DOCUMENTO_SAP',
    'SKU_VIVOGO', 'COD_MATERIAL_SAP', 'SERIAL_VIVOGO', 'SERIAL_SAP', 'ORDEM_SAP',
    'NUM_FAT_SAP', 'VALOR_SAP', 'VALOR_VIVO_GO',
)


def chave_de(linha):
    """sha1 das colunas de identidade da linha da view."""
    partes = []
    for coluna in COLUNAS_IDENTIDADE:
        valor = linha.get(coluna)
        partes.append('' if valor is None else str(valor))
    return hashlib.sha1('\x1f'.join(partes).encode('utf-8')).hexdigest()


class LinhaAuditoria(models.Model):
    """Uma divergência da auditoria, espelhada do SAP."""

    chave = models.CharField(max_length=40, unique=True, verbose_name='Chave')

    # ── o que veio do SAP (o resto da linha fica em `dados`) ────────────────
    tipo_erro = models.CharField(max_length=60, blank=True, db_index=True, verbose_name='Tipo do erro')
    data_venda = models.DateField(null=True, blank=True, db_index=True, verbose_name='Data da venda')
    pdv = models.CharField(max_length=120, blank=True, db_index=True, verbose_name='PDV')
    loja_vivogo = models.CharField(max_length=120, blank=True, verbose_name='Loja Vivo go')
    id_venda = models.CharField(max_length=60, blank=True, db_index=True, verbose_name='ID da venda')
    nome_cliente = models.CharField(max_length=180, blank=True, verbose_name='Cliente')
    documento_vivogo = models.CharField(max_length=30, blank=True, verbose_name='Documento')
    produto_vivogo = models.CharField(max_length=255, blank=True, verbose_name='Produto Vivo go')
    produto_sap = models.CharField(max_length=255, blank=True, verbose_name='Produto SAP')
    sku_vivogo = models.CharField(max_length=60, blank=True, verbose_name='SKU Vivo go')
    serial_vivogo = models.CharField(max_length=300, blank=True, verbose_name='Serial Vivo go')
    serial_sap = models.TextField(blank=True, verbose_name='Serial SAP')
    ordem_sap = models.CharField(max_length=30, blank=True, verbose_name='Ordem SAP')
    num_fat_sap = models.CharField(max_length=40, blank=True, verbose_name='Nota fiscal')
    status_nf = models.CharField(max_length=60, blank=True, verbose_name='Status da NF')
    valor_sap = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                    verbose_name='Valor SAP')
    valor_vivogo = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                       verbose_name='Valor Vivo go')
    diferenca_valor = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                          verbose_name='Diferença de valor')
    situacao_valor = models.CharField(max_length=180, blank=True, verbose_name='Situação do valor')
    status_documento = models.CharField(max_length=40, blank=True, verbose_name='Documento')
    status_produto = models.CharField(max_length=40, blank=True, verbose_name='Produto')
    status_valor = models.CharField(max_length=40, blank=True, verbose_name='Valor')
    status_loja = models.CharField(max_length=40, blank=True, verbose_name='Loja')
    status_data = models.CharField(max_length=40, blank=True, verbose_name='Data')
    diferenca_dias = models.IntegerField(null=True, blank=True, verbose_name='Diferença em dias')
    diferenca_explicita = models.TextField(blank=True, verbose_name='O que está diferente')
    pontuacao_indicios = models.IntegerField(null=True, blank=True, verbose_name='Pontuação dos indícios')
    # Vem da SAP.zs103_final (a view não traz), pela nota fiscal — 28/10/2026.
    forma_pagamento = models.CharField(max_length=80, blank=True, default='', db_default='',
                                       verbose_name='Forma de pagamento')
    dados = models.JSONField(default=dict, blank=True, verbose_name='Linha completa')

    # ── controle do espelho ─────────────────────────────────────────────────
    ativa = models.BooleanField(default=True, db_index=True, verbose_name='Ainda está na auditoria')
    visto_em = models.DateTimeField(default=timezone.now, verbose_name='Visto pela última vez em')
    sumiu_em = models.DateTimeField(null=True, blank=True, verbose_name='Sumiu da auditoria em')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # ── o que é do portal ───────────────────────────────────────────────────
    resolvida = models.BooleanField(default=False, db_index=True, verbose_name='Resolvida')
    resolvida_em = models.DateTimeField(null=True, blank=True, verbose_name='Marcada em')
    resolvida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='linhas_sap_marcadas', verbose_name='Quem marcou')
    observacao = models.TextField(blank=True, verbose_name='Observação')

    class Meta:
        verbose_name = 'Linha da auditoria SAP'
        verbose_name_plural = 'Linhas da auditoria SAP'
        ordering = ['-data_venda', 'pdv', 'id_venda']
        indexes = [
            models.Index(fields=['ativa', 'resolvida']),
            models.Index(fields=['tipo_erro', 'data_venda']),
            models.Index(fields=['pdv', 'data_venda']),
        ]

    def __str__(self):
        return f'{self.tipo_erro} — venda {self.id_venda or "?"} ({self.pdv})'

    @property
    def diferenca_em_reais(self):
        """Quanto de dinheiro está em jogo nesta linha (sempre positivo)."""
        return abs(self.diferenca_valor) if self.diferenca_valor is not None else None

    @property
    def situacao(self):
        return 'RESOLVIDA' if self.resolvida else 'ABERTA'

    @property
    def voltou(self):
        """Foi marcada como resolvida mas continua aparecendo na auditoria."""
        return bool(self.resolvida and self.ativa)

    def estado_da_resolucao(self, corte):
        """'pendente' (marcada e o SAP leu depois e ela continua lá), 'aguardando'
        (marcada depois da última leitura) ou '' (aberta, ou já saiu do SAP).

        `corte`: início da última leitura do SAP que deu certo (``inicio_da_ultima_leitura``).
        """
        if not (self.resolvida and self.ativa):
            return ''
        if corte and self.resolvida_em and self.resolvida_em < corte:
            return 'pendente'
        return 'aguardando'

    def marcar(self, usuario, resolvida, observacao=''):
        """Marca (ou desmarca) como resolvida, guardando quem foi.

        Toda passagem fica no histórico: desmarcar não apaga quem tinha
        marcado, senão a pergunta "quem deu isso como resolvido?" ficaria sem
        resposta justamente quando ela importa.
        """
        self.resolvida = bool(resolvida)
        self.resolvida_em = timezone.now()
        self.resolvida_por = usuario if getattr(usuario, 'pk', None) else None
        if observacao:
            self.observacao = observacao[:2000]
        self.save(update_fields=['resolvida', 'resolvida_em', 'resolvida_por',
                                 'observacao', 'atualizado_em'])
        return MarcacaoAuditoria.objects.create(
            linha=self, usuario=self.resolvida_por, resolvida=self.resolvida,
            observacao=(observacao or '')[:2000])


class MarcacaoAuditoria(models.Model):
    """Cada vez que alguém marcou ou desmarcou uma linha."""

    linha = models.ForeignKey(LinhaAuditoria, on_delete=models.CASCADE,
                              related_name='marcacoes', verbose_name='Linha')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='marcacoes_sap', verbose_name='Quem')
    resolvida = models.BooleanField(verbose_name='Marcou como resolvida')
    observacao = models.TextField(blank=True, verbose_name='Observação')
    quando = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Quando')

    class Meta:
        verbose_name = 'Marcação da auditoria SAP'
        verbose_name_plural = 'Marcações da auditoria SAP'
        ordering = ['-quando']

    def __str__(self):
        quem = self.usuario.full_name if self.usuario else 'alguém'
        return f'{quem} {"resolveu" if self.resolvida else "reabriu"} em {self.quando:%d/%m/%Y %H:%M}'


# Divergência de valor abaixo de R$ 1,00 não é problema para tratar: arredondamento,
# centavo de imposto. A linha do tipo VALOR com diferença menor que isso sai da lista,
# do painel e dos filtros (continua no espelho — se a diferença crescer, ela volta).
LIMITE_VALOR_DESPREZIVEL = Decimal('1.00')


# O que a loja de fato precisa ajustar (28/10/2026). Os outros tipos da auditoria
# (valor, produto, documento, data, loja, bônus fabricante) continuam no espelho,
# mas saem da Visão SAP. Cada grupo é uma aba.
GRUPOS_DA_TELA = (
    ('serial', 'Serial não encontrado', ('SERIAL ERRADO NO VIVO GO', 'SERIAL AUSENTE NO VIVO GO')),
    ('vivogo', 'Lançado no SAP e não no Vivo GO', ('VENDA NÃO ENCONTRADA NO VIVO GO',)),
    ('sem_nf', 'Sem NF', ('SEM NF',)),
)
TIPOS_DA_TELA = tuple(t for _c, _r, tipos in GRUPOS_DA_TELA for t in tipos)


def q_da_tela(grupo=''):
    """Só os tipos que a Visão SAP mostra; com ``grupo``, só os daquela aba."""
    tipos = dict((c, t) for c, _r, t in GRUPOS_DA_TELA).get(grupo, TIPOS_DA_TELA)
    return models.Q(tipo_erro__in=tipos)


def q_valor_desprezivel():
    limite = LIMITE_VALOR_DESPREZIVEL
    return models.Q(tipo_erro__iexact='VALOR', diferenca_valor__gt=-limite, diferenca_valor__lt=limite)


def inicio_da_ultima_leitura():
    """Quando começou a última leitura do SAP que deu certo (ou None).

    É a régua do "resolvido mas ainda pendente": quem marcou antes desse instante
    já passou por uma leitura — se a linha continua ativa, o problema não saiu do
    SAP. Conta do início (quando − duração): marcação feita durante a leitura não
    foi vista por ela.
    """
    from datetime import timedelta
    leitura = SincronizacaoAuditoria.objects.filter(erro='').only('quando', 'segundos').first()
    if leitura is None:
        return None
    return leitura.quando - timedelta(seconds=leitura.segundos or 0)


def filtro_resolvidas_pendentes(corte):
    """Marcadas como resolvidas, lidas de novo pelo SAP depois disso, e ainda lá."""
    if corte is None:
        return models.Q(pk__in=[])
    return models.Q(resolvida=True, ativa=True, resolvida_em__lt=corte)


def filtro_aguardando_leitura(corte):
    """Marcadas como resolvidas depois da última leitura: a próxima é que vai dizer."""
    base = models.Q(resolvida=True, ativa=True)
    if corte is None:
        return base
    return base & (models.Q(resolvida_em__gte=corte) | models.Q(resolvida_em__isnull=True))


class SincronizacaoAuditoria(models.Model):
    """Uma leitura do MySQL do SAP — para a tela dizer de quando são os dados."""

    quando = models.DateTimeField(auto_now_add=True, verbose_name='Quando')
    por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sincronizacoes_sap', verbose_name='Quem mandou atualizar')
    novas = models.IntegerField(default=0, verbose_name='Linhas novas')
    atualizadas = models.IntegerField(default=0, verbose_name='Linhas que mudaram')
    sumiram = models.IntegerField(default=0, verbose_name='Linhas que saíram')
    total = models.IntegerField(default=0, verbose_name='Linhas na auditoria')
    segundos = models.FloatField(default=0, verbose_name='Tempo (s)')
    erro = models.TextField(blank=True, verbose_name='Erro')

    class Meta:
        verbose_name = 'Sincronização da auditoria SAP'
        verbose_name_plural = 'Sincronizações da auditoria SAP'
        ordering = ['-quando']

    def __str__(self):
        return f'{self.quando:%d/%m/%Y %H:%M} — {self.total} linhas'

    @property
    def deu_certo(self):
        return not self.erro


class AgendaLeituraSap(models.Model):
    """A leitura automática do SAP (uma linha só).

    Produção não tem cron: a primeira visita ao portal depois do intervalo dispara
    a leitura numa thread (``auditoria_sap/agendador.py``). ``ultima_automatica``
    é o carimbo que os workers disputam num UPDATE condicional — só um ganha.
    """

    ativo = models.BooleanField(default=True, verbose_name='Ler o SAP automaticamente')
    intervalo_horas = models.PositiveSmallIntegerField(default=3, verbose_name='A cada quantas horas')
    ultima_automatica = models.DateTimeField(null=True, blank=True, verbose_name='Última leitura automática')
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        verbose_name = 'Agenda da leitura do SAP'
        verbose_name_plural = 'Agenda da leitura do SAP'

    def __str__(self):
        return f'Leitura do SAP a cada {self.intervalo_horas} h'

    @classmethod
    def get(cls):
        agenda, _ = cls.objects.get_or_create(pk=1)
        return agenda


# ---------------------------------------------------------------------------
# Visão Renova (09/11/2026): toda venda com Renova no SAP (ordem ZVNB) precisa de
# um chamado de Renova (Allied/Vini) no portal. O casamento é pelo CPF do cliente
# (auditoria_sap/renova.py) e a loja sem chamado é cobrada no grupo de gestão às 15h.
# ---------------------------------------------------------------------------
class VendaRenova(models.Model):
    """Uma ordem ZVNB do SAP (zs103_final), espelhada a cada leitura do SAP."""

    COMO_VENDA = 'venda'
    COMO_CPF = 'cpf'
    COMO_FICHA = 'ficha'
    COMO_MANUAL = 'manual'
    COMO = (
        (COMO_VENDA, 'Venda do Vivo Go do cliente citada no chamado'),
        (COMO_CPF, 'CPF do cliente escrito no chamado'),
        (COMO_FICHA, 'Ficha do Renova (app) ligada ao chamado'),
        (COMO_MANUAL, 'Ligado à mão'),
    )

    ordem = models.CharField(max_length=30, unique=True, verbose_name='Ordem ZVNB (doc. de vendas)')
    num_fat = models.CharField(max_length=40, blank=True, db_index=True, verbose_name='Nota fiscal')
    data_fatura = models.DateField(null=True, blank=True, db_index=True, verbose_name='Data da fatura')
    cpf = models.CharField(max_length=14, blank=True, db_index=True, verbose_name='CPF/CNPJ do cliente (só dígitos)')
    cliente_nome = models.CharField(max_length=180, blank=True, verbose_name='Cliente')
    escritorio = models.CharField(max_length=10, blank=True, verbose_name='Escritório de vendas (SAP)')
    loja = models.CharField(max_length=120, blank=True, db_index=True, verbose_name='Loja')
    setor = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='+', verbose_name='Loja no portal')
    material = models.CharField(max_length=255, blank=True, verbose_name='Aparelho vendido')
    imei = models.CharField(max_length=40, blank=True, verbose_name='IMEI do aparelho vendido')
    valor_faturado = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                         verbose_name='Valor faturado')
    desconto = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                   verbose_name='Desconto (Renova)')
    vendas_vivogo = models.JSONField(default=list, blank=True, verbose_name='Vendas do Vivo Go do cliente')

    chamado = models.ForeignKey('tickets.Ticket', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='vendas_renova_sap', verbose_name='Chamado de Renova')
    chamado_como = models.CharField(max_length=10, choices=COMO, blank=True, default='', db_default='',
                                    verbose_name='Como o chamado foi achado')
    chamado_achado_em = models.DateTimeField(null=True, blank=True, verbose_name='Chamado achado em')

    dispensada = models.BooleanField(default=False, db_default=False, db_index=True,
                                     verbose_name='Não precisa de chamado')
    dispensada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       blank=True, related_name='+')
    dispensada_em = models.DateTimeField(null=True, blank=True)
    observacao = models.TextField(blank=True, default='', db_default='', verbose_name='Observação')

    cobrancas = models.PositiveSmallIntegerField(default=0, db_default=0, verbose_name='Vezes cobrada no grupo')
    ultima_cobranca_em = models.DateTimeField(null=True, blank=True, verbose_name='Última cobrança')

    ativa = models.BooleanField(default=True, db_default=True, verbose_name='Ainda está no SAP')
    visto_em = models.DateTimeField(default=timezone.now, verbose_name='Visto no SAP em')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Venda com Renova (SAP)'
        verbose_name_plural = 'Vendas com Renova (SAP)'
        ordering = ['-data_fatura', '-ordem']

    def __str__(self):
        return f'ZVNB {self.ordem} ({self.loja or "sem loja"})'

    @property
    def pendente(self):
        return self.chamado_id is None and not self.dispensada

    @property
    def cpf_mascarado(self):
        d = self.cpf or ''
        if len(d) == 11:
            return f'***.{d[3:6]}.{d[6:9]}-**'
        return d


class ConfiguracaoRenovaSap(models.Model):
    """A cobrança da Visão Renova (uma linha só). Nasce desligada."""

    cobranca_ativa = models.BooleanField(default=False, db_default=False,
                                         verbose_name='Cobrar no grupo da loja todo dia às 15h')
    janela_dias = models.PositiveSmallIntegerField(default=7, db_default=7,
                                                   verbose_name='Cobrar vendas dos últimos N dias')
    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       blank=True, related_name='+')

    class Meta:
        verbose_name = 'Configuração da Visão Renova'
        verbose_name_plural = 'Configuração da Visão Renova'

    @classmethod
    def get(cls):
        cfg, _ = cls.objects.get_or_create(pk=1)
        return cfg


class CobrancaRenova(models.Model):
    """Uma mensagem para o grupo de gestão de uma loja — gravada antes de sair."""

    AUTOMATICA = 'auto'
    MANUAL = 'manual'
    ORIGENS = ((AUTOMATICA, 'Automática (15h)'), (MANUAL, 'Pela tela'))

    setor = models.ForeignKey('users.Sector', on_delete=models.CASCADE, related_name='+')
    grupo = models.CharField(max_length=80)
    texto = models.TextField()
    vendas = models.ManyToManyField(VendaRenova, related_name='cobrancas_feitas', blank=True)
    origem = models.CharField(max_length=10, choices=ORIGENS, default=AUTOMATICA)
    disparado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                      blank=True, related_name='+')
    enviado = models.BooleanField(null=True, verbose_name='Saiu? (vazio = mandando)')
    detalhe = models.CharField(max_length=255, blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Cobrança de Renova no WhatsApp'
        verbose_name_plural = 'Cobranças de Renova no WhatsApp'
        ordering = ['-criado_em']
