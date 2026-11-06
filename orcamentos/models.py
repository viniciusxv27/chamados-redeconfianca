"""Orçamentos: módulo próprio do vendedor (saiu de Vendas em 06/11/2026).

O orçamento em si continua na tabela de sempre (``vendas.Orcamento``) — o
histórico não muda de lugar. Aqui ficam o que o módulo novo acrescenta:

- quem gere (gestores escolhidos pelo SUPERADMIN, com as lojas de cada um) e
  quem mais pode usar sem estar lotado em loja;
- o Mailing de cada loja: bases importadas (planilha/CSV, escolhendo a coluna do
  número) e os contatos que os vendedores da loja trabalham.
"""
from django.conf import settings
from django.db import models


class GestorOrcamentos(models.Model):
    """Quem importa mailing e vê o painel — das lojas marcadas (nenhuma marcada = todas)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='gestor_orcamentos')
    lojas = models.ManyToManyField('users.Sector', blank=True, related_name='+')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Gestor de orçamentos'
        verbose_name_plural = 'Gestores de orçamentos'

    def __str__(self):
        return f'Gestor: {self.user}'


class LiberacaoOrcamentos(models.Model):
    """Quem usa o módulo sem estar lotado em loja (escritório, apoio)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='liberacao_orcamentos')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Liberação de orçamentos'
        verbose_name_plural = 'Liberações de orçamentos'


class BaseMailing(models.Model):
    """Uma planilha importada para o Mailing de uma loja."""

    RASCUNHO, ATIVA, ENCERRADA = 'RASCUNHO', 'ATIVA', 'ENCERRADA'
    STATUS = [(RASCUNHO, 'Aguardando conferência'), (ATIVA, 'Ativa'), (ENCERRADA, 'Encerrada')]

    nome = models.CharField(max_length=120)
    loja = models.ForeignKey('users.Sector', on_delete=models.CASCADE, null=True, blank=True,
                             related_name='bases_mailing')
    status = models.CharField(max_length=10, choices=STATUS, default=RASCUNHO, db_index=True)
    arquivo_nome = models.CharField(max_length=255, blank=True)
    colunas = models.JSONField(default=list, blank=True)
    # As linhas lidas ficam aqui só até a conferência (depois viram contatos e isto é limpo).
    linhas = models.JSONField(default=list, blank=True)
    coluna_numero = models.PositiveSmallIntegerField(null=True, blank=True)
    coluna_nome = models.PositiveSmallIntegerField(null=True, blank=True)
    total_linhas = models.PositiveIntegerField(default=0)
    importados = models.PositiveIntegerField(default=0)
    duplicados = models.PositiveIntegerField(default=0)
    invalidos = models.PositiveIntegerField(default=0)
    importado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    confirmado_em = models.DateTimeField(null=True, blank=True)
    encerrado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'Base de mailing'
        verbose_name_plural = 'Bases de mailing'

    def __str__(self):
        return f'{self.nome} ({self.loja or "sem loja"})'


class ContatoMailing(models.Model):
    """Um cliente do mailing da loja, para o vendedor fazer orçamento."""

    NOVO, TRABALHADO, DESCARTADO = 'NOVO', 'TRABALHADO', 'DESCARTADO'
    STATUS = [(NOVO, 'A trabalhar'), (TRABALHADO, 'Orçamento feito'), (DESCARTADO, 'Descartado')]

    base = models.ForeignKey(BaseMailing, on_delete=models.CASCADE, related_name='contatos')
    loja = models.ForeignKey('users.Sector', on_delete=models.CASCADE, related_name='contatos_mailing')
    numero = models.CharField(max_length=20, db_index=True)
    nome = models.CharField(max_length=200, blank=True)
    dados = models.JSONField(default=dict, blank=True, help_text='As outras colunas da planilha.')
    status = models.CharField(max_length=10, choices=STATUS, default=NOVO, db_index=True)
    trabalhado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    trabalhado_em = models.DateTimeField(null=True, blank=True)
    orcamento = models.ForeignKey('vendas.Orcamento', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='contatos_mailing')
    observacao = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['status', 'nome', 'id']
        constraints = [models.UniqueConstraint(fields=['base', 'numero'], name='contato_mailing_unico_na_base')]
        indexes = [models.Index(fields=['loja', 'status'])]
        verbose_name = 'Contato do mailing'
        verbose_name_plural = 'Contatos do mailing'

    def __str__(self):
        return f'{self.nome or self.numero} · {self.loja}'

    @property
    def numero_formatado(self):
        d = self.numero[2:] if self.numero.startswith('55') and len(self.numero) in (12, 13) else self.numero
        if len(d) == 11:
            return f'({d[:2]}) {d[2:7]}-{d[7:]}'
        if len(d) == 10:
            return f'({d[:2]}) {d[2:6]}-{d[6:]}'
        return self.numero
