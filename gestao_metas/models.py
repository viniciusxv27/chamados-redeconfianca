"""Gerenciamento de metas: a meta do mês de cada loja, pilar a pilar.

Substitui a planilha "METAS <mês>" que a Inteligência montava à mão: uma aba por
pilar, uma linha por loja, o realizado dos últimos meses, a média do
quadrimestre, a meta da Vivo, a meta do mês anterior e a meta que se vai dar.

Nada aqui depende do Power BI (/power-bi/manage/metas/): é outro arquivo, outra
finalidade. O histórico mora no próprio módulo — ``Realizado`` por loja, mês e
pilar, e ``MetaLoja`` por competência — e cada competência nova já nasce com a
janela certa (quadrimestre, mesmo mês do ano passado, meta do mês anterior),
sem arrastar fórmula para o lado.
"""
from django.conf import settings
from django.db import models

PILARES = [
    ('movel', 'Móvel'),
    ('fixa', 'Fixa'),
    ('sva', 'SVA'),
    ('seguros', 'Seguros'),
    ('smartphone', 'Smartphone'),
    ('eletronicos', 'Eletrônicos'),
    ('essenciais', 'Essenciais'),
]
PILARES_DICT = dict(PILARES)
# Nome da aba na planilha (e no Excel exportado).
ABA_DO_PILAR = {
    'movel': 'MÓVEL', 'fixa': 'FIXA', 'sva': 'SVA', 'seguros': 'SEGUROS', 'smartphone': 'SMARTPHONE',
    'eletronicos': 'ELETRONICOS', 'essenciais': 'ESSENCIAIS',
}
PILARES_PRODUTO = ('smartphone', 'eletronicos', 'essenciais')
PILARES_SERVICO = ('movel', 'fixa', 'sva', 'seguros')

REGIOES = [('RJ', 'RJ'), ('ES', 'ES'), ('SP', 'SP')]


class AcessoGestaoMetas(models.Model):
    """Quem entra além do SUPERADMIN — uma pessoa ou um grupo de comunicação."""

    EDITOR = 'EDITOR'
    VISUALIZADOR = 'VISUALIZADOR'
    PAPEIS = [(EDITOR, 'Editor'), (VISUALIZADOR, 'Visualizador')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='acesso_gestao_metas', verbose_name='Pessoa')
    grupo = models.OneToOneField('communications.CommunicationGroup', on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='acesso_gestao_metas', verbose_name='Grupo')
    papel = models.CharField(max_length=12, choices=PAPEIS, default=VISUALIZADOR)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Acesso ao Gerenciamento de metas'
        verbose_name_plural = 'Acessos ao Gerenciamento de metas'
        constraints = [models.CheckConstraint(
            condition=(models.Q(user__isnull=False, grupo__isnull=True) | models.Q(user__isnull=True, grupo__isnull=False)),
            name='gestao_metas_acesso_pessoa_ou_grupo')]

    def __str__(self):
        return f'{self.user or self.grupo} — {self.get_papel_display()}'


class LojaMeta(models.Model):
    """Uma linha da planilha: a loja como ela aparece nas bases (PDV REDE) e na Vivo (PDV VIVO)."""

    nome = models.CharField('PDV rede', max_length=120)
    chave = models.CharField(max_length=120, unique=True, help_text='Nome normalizado (sem "Loja", sem acento).')
    pdv_vivo = models.CharField('PDV Vivo', max_length=160, blank=True)
    apelidos = models.CharField('Outros nomes', max_length=400, blank=True,
                                help_text='Separados por vírgula: como a loja aparece em outras bases.')
    setor = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='lojas_gestao_metas')
    regiao = models.CharField(max_length=2, choices=REGIOES, blank=True)
    ordem = models.PositiveIntegerField(default=0)
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ['ordem', 'nome']
        verbose_name = 'Loja (metas)'
        verbose_name_plural = 'Lojas (metas)'

    def __str__(self):
        return self.nome


class Competencia(models.Model):
    """O mês para o qual se dá a meta. Fechada = só leitura e vira a "meta mês anterior" da seguinte."""

    ABERTA = 'ABERTA'
    FECHADA = 'FECHADA'
    STATUS = [(ABERTA, 'Aberta'), (FECHADA, 'Fechada')]

    mes = models.DateField(unique=True, help_text='Sempre o dia 1º do mês.')
    status = models.CharField(max_length=8, choices=STATUS, default=ABERTA)
    observacao = models.CharField(max_length=300, blank=True)
    criada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criada_em = models.DateTimeField(auto_now_add=True)
    fechada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    fechada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-mes']
        verbose_name = 'Competência de metas'
        verbose_name_plural = 'Competências de metas'

    def __str__(self):
        return self.mes.strftime('%m/%Y')

    @property
    def fechada(self):
        return self.status == self.FECHADA

    @property
    def codigo(self):
        return self.mes.strftime('%Y-%m')


class Realizado(models.Model):
    """Quanto a loja fez num mês, num pilar. Fixa guarda também as quantidades (TV e Fixa)."""

    PLANILHA = 'PLANILHA'
    BASE = 'BASE'
    BANCO = 'BANCO'
    MANUAL = 'MANUAL'
    FONTES = [(PLANILHA, 'Planilha antiga'), (BASE, 'Base de vendas'), (BANCO, 'Banco de vendas'), (MANUAL, 'Manual')]

    loja = models.ForeignKey(LojaMeta, on_delete=models.CASCADE, related_name='realizados')
    mes = models.DateField()
    pilar = models.CharField(max_length=12, choices=PILARES)
    valor = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    qtd_tv = models.IntegerField(null=True, blank=True)
    qtd_fixa = models.IntegerField(null=True, blank=True)
    qtd_total = models.IntegerField(null=True, blank=True,
                                    help_text='Só quando não se sabe a divisão TV/Fixa (ex.: "real ano anterior" da planilha).')
    fonte = models.CharField(max_length=10, choices=FONTES, default=MANUAL)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['mes', 'pilar']
        verbose_name = 'Realizado (metas)'
        verbose_name_plural = 'Realizados (metas)'
        constraints = [models.UniqueConstraint(fields=['loja', 'mes', 'pilar'], name='gestao_metas_realizado_unico')]

    def __str__(self):
        return f'{self.loja} {self.mes:%m/%Y} {self.pilar}'

    @property
    def quantidade(self):
        if self.qtd_tv is not None or self.qtd_fixa is not None:
            return (self.qtd_tv or 0) + (self.qtd_fixa or 0)
        return self.qtd_total


class MetaLoja(models.Model):
    """A linha de uma loja num pilar, numa competência.

    Fixa segue a planilha: ``meta`` é em reais; a quantidade sai do ticket
    médio, é dividida pelos consultores (``qtd_cns``), arredondada para cima ou
    para baixo (``decisao``) e volta multiplicada — essa é a ``nova_meta``, em
    quantidade, que vale como meta do mês.
    """

    CIMA = 'CIMA'
    BAIXO = 'BAIXO'
    DECISOES = [(CIMA, 'Cima'), (BAIXO, 'Baixo')]

    competencia = models.ForeignKey(Competencia, on_delete=models.CASCADE, related_name='metas')
    loja = models.ForeignKey(LojaMeta, on_delete=models.CASCADE, related_name='metas')
    pilar = models.CharField(max_length=12, choices=PILARES)
    meta = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    meta_vivo = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    meta_anterior_manual = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text='Meta do mês anterior trazida da planilha antiga, quando o mês anterior não existe no módulo.')
    qtd_cns = models.PositiveIntegerField(null=True, blank=True)
    decisao = models.CharField(max_length=5, choices=DECISOES, default=CIMA)
    nova_meta = models.IntegerField(null=True, blank=True, help_text='Fixa: a meta em quantidade (calculada).')
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Meta da loja'
        verbose_name_plural = 'Metas das lojas'
        constraints = [models.UniqueConstraint(fields=['competencia', 'loja', 'pilar'], name='gestao_metas_meta_unica')]

    def __str__(self):
        return f'{self.competencia} {self.loja} {self.pilar}'

    @property
    def meta_final(self):
        """O que vale como meta do mês (e vira "meta mês anterior" da competência seguinte)."""
        if self.pilar == 'fixa':
            return self.nova_meta
        return self.meta


class HistoricoMeta(models.Model):
    """Quem mudou o quê, quando. Os nomes ficam gravados (a pessoa pode sair do portal)."""

    competencia = models.ForeignKey(Competencia, on_delete=models.CASCADE, related_name='historico')
    loja = models.ForeignKey(LojaMeta, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    loja_nome = models.CharField(max_length=120, blank=True)
    pilar = models.CharField(max_length=12, blank=True)
    campo = models.CharField(max_length=30)
    antes = models.CharField(max_length=60, blank=True)
    depois = models.CharField(max_length=60, blank=True)
    origem = models.CharField(max_length=60, blank=True, help_text='Digitado, preenchimento rápido, importação…')
    por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                            related_name='+')
    por_nome = models.CharField(max_length=200, blank=True)
    quando = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-quando', '-id']
        verbose_name = 'Histórico de meta'
        verbose_name_plural = 'Histórico de metas'


class ImportacaoMetas(models.Model):
    """Um arquivo (ou uma busca no banco) lido e guardado para conferir antes de gravar."""

    BASE = 'BASE'
    BANCO = 'BANCO'
    META_VIVO = 'META_VIVO'
    PLANILHA = 'PLANILHA'
    TIPOS = [(BASE, 'Base de vendas (produtos/serviços)'), (BANCO, 'Banco de vendas (Vivo GO)'),
             (META_VIVO, 'Arquivo de metas da Vivo'), (PLANILHA, 'Planilha antiga de metas')]
    PREVIA = 'PREVIA'
    APLICADA = 'APLICADA'
    DESCARTADA = 'DESCARTADA'
    STATUS = [(PREVIA, 'Aguardando conferência'), (APLICADA, 'Gravada'), (DESCARTADA, 'Descartada')]

    tipo = models.CharField(max_length=10, choices=TIPOS)
    status = models.CharField(max_length=10, choices=STATUS, default=PREVIA)
    competencia = models.ForeignKey(Competencia, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='importacoes')
    arquivo_nome = models.CharField(max_length=255, blank=True)
    dados = models.JSONField(default=dict, blank=True)
    resumo = models.CharField(max_length=400, blank=True)
    substituir = models.BooleanField(default=True, help_text='Grava por cima do que já existe.')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    aplicado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-criado_em', '-id']
        verbose_name = 'Importação de metas'
        verbose_name_plural = 'Importações de metas'

    def __str__(self):
        return f'{self.get_tipo_display()} — {self.arquivo_nome}'
