"""Modelos do módulo Fibras (acompanhamento de vendas Fixa)."""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Fibra(models.Model):
    """Snapshot local de uma venda de Fibra (originada do MySQL `vendas_servicos`).

    A linha é sincronizada periodicamente; status/observações da Myrella ficam
    em `FibraStatus`/`FibraObservacao` para não serem perdidos no resync.
    """

    STATUS_AGENDADO = 'agendado'
    STATUS_PENDENTE = 'pendente'
    STATUS_PROBLEMA = 'problema'
    STATUS_INSTALADO = 'instalado'
    STATUS_CANCELADO = 'cancelado'

    STATUS_CHOICES = [
        (STATUS_AGENDADO, 'Agendado'),
        (STATUS_PENDENTE, 'Pendente'),
        (STATUS_PROBLEMA, 'Vendas com problema'),
        (STATUS_INSTALADO, 'Instalado'),
        (STATUS_CANCELADO, 'Cancelado'),
    ]

    numero_da_venda = models.CharField('Número da venda', max_length=60, unique=True)
    numero_protocolo = models.CharField(
        'Nº do protocolo', max_length=60, blank=True, db_index=True,
        help_text='Usado para casar com a planilha diária (coluna Nº_protocolo).',
    )
    cpf = models.CharField('CPF', max_length=20, blank=True)
    cliente = models.CharField('Nome do cliente', max_length=200, blank=True)
    endereco = models.CharField('Endereço', max_length=300, blank=True)
    numero_acesso = models.CharField('Número de acesso', max_length=60, blank=True)
    plano = models.CharField('Plano / produto', max_length=200, blank=True)
    valor = models.DecimalField('Valor', max_digits=12, decimal_places=2, default=0)
    pdv = models.CharField('PDV', max_length=120, blank=True, db_index=True)
    vendedor = models.CharField('Vendedor (nome)', max_length=200, blank=True, db_index=True)
    data_da_venda = models.DateField('Data da venda', null=True, blank=True)
    pilar = models.CharField('Pilar', max_length=40, blank=True)
    servico_tecnico = models.CharField('Serviço Técnico', max_length=80, blank=True)

    # Status e retorno da Myrella
    status = models.CharField(
        'Status (Myrella)', max_length=20, choices=STATUS_CHOICES,
        default=STATUS_PENDENTE, db_index=True,
    )
    retorno_myrella = models.TextField('Retorno da Myrella', blank=True)

    # Bookkeeping
    first_seen_at = models.DateTimeField('Primeira sincronização', default=timezone.now)
    last_synced_at = models.DateTimeField('Última sincronização', auto_now=True)

    # Importação da planilha diária (visual, define ordem no kanban)
    ordem_planilha = models.IntegerField(
        'Ordem (planilha)', null=True, blank=True, db_index=True,
        help_text='Posição na planilha do último import. Define a ordem visual no Kanban.',
    )
    last_planilha_at = models.DateTimeField(
        'Última atualização via planilha', null=True, blank=True,
    )
    status_planilha_raw = models.CharField(
        'Status bruto da planilha', max_length=120, blank=True,
        help_text='Texto original da coluna de status da planilha (auditoria).',
    )
    sla_agenda = models.CharField(
        'SLA Agenda (planilha)', max_length=120, blank=True,
        help_text='Valor da coluna SLA AGENDA na última importação.',
    )
    motivo_planilha = models.CharField(
        'Motivo (planilha)', max_length=255, blank=True,
        help_text='Valor da coluna MOTIVO na última importação (vendas com problema).',
    )
    etapa_instalacao = models.CharField('Etapa instalação (Esteira)', max_length=120, blank=True)

    # Cruzamento com a Esteira de Vendas Vivo (fibras/validacao.py)
    cruzamento = models.ForeignKey('Cruzamento', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='fibras', help_text='Último cruzamento em que a venda entrou.')
    adabas = models.CharField('Loja ADABAS', max_length=40, blank=True)
    protocolo_tratado = models.CharField(max_length=60, blank=True)
    formato_protocolo = models.CharField(max_length=60, blank=True)
    status_cruzamento = models.CharField(max_length=30, blank=True, db_index=True,
                                         help_text='Localizada / Não localizada / Venda B2B')
    assertividade = models.CharField(max_length=20, blank=True, help_text='Alta / Média / Muito baixa / Manual')
    ordem_vinculada = models.CharField('ORDEM vinculada', max_length=40, blank=True, db_index=True)
    metodo_cruzamento = models.CharField(max_length=80, blank=True)
    motivo_cruzamento = models.TextField(blank=True)
    observacao_cruzamento = models.TextField(blank=True)
    conferencia_endereco = models.CharField(max_length=40, blank=True)
    detalhe_endereco = models.TextField(blank=True)
    # Ajustes manuais: valem para todos os próximos cruzamentos
    ordem_manual = models.CharField('ORDEM confirmada/vinculada à mão', max_length=40, blank=True)
    ordens_rejeitadas = models.JSONField(default=list, blank=True)
    comentario_cruzamento = models.TextField('Comentário do cruzamento', blank=True)

    class Meta:
        verbose_name = 'Fibra'
        verbose_name_plural = 'Fibras'
        ordering = ['-data_da_venda', '-id']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['data_da_venda']),
        ]

    def __str__(self):
        return f"{self.numero_da_venda} - {self.cliente or 'sem nome'}"


class FibraStatusHistory(models.Model):
    """Histórico de mudanças de status da fibra (para auditoria)."""
    fibra = models.ForeignKey(Fibra, on_delete=models.CASCADE, related_name='status_history')
    status_anterior = models.CharField(max_length=20, blank=True)
    status_novo = models.CharField(max_length=20)
    retorno = models.TextField(blank=True)
    alterado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    alterado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-alterado_em']
        verbose_name = 'Histórico de status (Fibra)'
        verbose_name_plural = 'Históricos de status (Fibra)'

    def __str__(self):
        return f"{self.fibra.numero_da_venda}: {self.status_anterior} → {self.status_novo}"


class FibraIncidente(models.Model):
    """Incidente aberto pelo Consultor/Negócio (CN) sobre uma fibra.

    Tabela própria (não usa app `tickets`).
    """
    STATUS_ABERTO = 'aberto'
    STATUS_EM_TRATATIVA = 'em_tratativa'
    STATUS_RESOLVIDO = 'resolvido'
    STATUS_CHOICES = [
        (STATUS_ABERTO, 'Aberto'),
        (STATUS_EM_TRATATIVA, 'Em tratativa'),
        (STATUS_RESOLVIDO, 'Resolvido'),
    ]

    fibra = models.ForeignKey(Fibra, on_delete=models.CASCADE, related_name='incidentes')
    aberto_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='fibra_incidentes_abertos',
    )
    observacao = models.TextField('Observação do CN')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ABERTO)
    resposta = models.TextField('Resposta da Ilha', blank=True)
    respondido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    aberto_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    # Chat unificado no /projects/support/admin/template/ (categoria "SUPORTE FIXA").
    # Quando preenchido, as mensagens da tratativa moram em SupportChatMessage.
    support_chat = models.OneToOneField(
        'projects.SupportChat', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='fibra_incidente',
    )
    # Última visualização da tratativa pelo autor (vendedor/gerente), para
    # destacar visualmente quando há resposta nova da atendente do suporte.
    last_opener_view_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-aberto_em']
        verbose_name = 'Incidente de Fibra'
        verbose_name_plural = 'Incidentes de Fibra'

    def __str__(self):
        return f"Incidente {self.id} - {self.fibra.numero_da_venda}"

    @property
    def has_unread_reply_for_opener(self) -> bool:
        """True quando há resposta da atendente do suporte que o autor não viu."""
        ref = self.last_opener_view_at
        if self.support_chat_id:
            qs = self.support_chat.messages.all()
            if self.aberto_por_id:
                qs = qs.exclude(user_id=self.aberto_por_id)
            if ref:
                qs = qs.filter(created_at__gt=ref)
            return qs.exists()
        # Fallback: usa o chat reverso antigo (FibraChat).
        try:
            chat = self.fibra.chat
        except Exception:
            return False
        qs = chat.mensagens.all()
        if self.aberto_por_id:
            qs = qs.exclude(autor_id=self.aberto_por_id)
        if ref:
            qs = qs.filter(criado_em__gt=ref)
        return qs.exists()


class FibraChat(models.Model):
    """Chat reverso entre Myrella (Ilha) e o colaborador, vinculado à fibra."""
    fibra = models.OneToOneField(Fibra, on_delete=models.CASCADE, related_name='chat')
    aberto_em = models.DateTimeField(auto_now_add=True)
    encerrado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Chat (Fibra)'
        verbose_name_plural = 'Chats (Fibra)'

    def __str__(self):
        return f"Chat Fibra {self.fibra.numero_da_venda}"


class FibraChatMessage(models.Model):
    chat = models.ForeignKey(FibraChat, on_delete=models.CASCADE, related_name='mensagens')
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='+',
    )
    texto = models.TextField()
    criado_em = models.DateTimeField(auto_now_add=True)
    lida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['criado_em']

    def __str__(self):
        return f"Msg {self.id} ({self.chat_id})"


class PlanilhaOrdemInconsistente(models.Model):
    """Número de ORDEM (planilha) que não foi encontrado em nenhuma Fibra local.

    Persiste entre importações; é removido automaticamente quando o protocolo
    correspondente passa a existir no banco e bate com algum ORDEM da próxima
    importação da planilha.
    """
    ordem = models.CharField(max_length=60, unique=True, db_index=True)
    status_raw = models.CharField(max_length=120, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    occurrences = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['-last_seen_at']
        verbose_name = 'Ordem inconsistente (planilha)'
        verbose_name_plural = 'Ordens inconsistentes (planilha)'

    def __str__(self):
        return f"Ordem {self.ordem} (sem match)"


# ---------------------------------------------------------------------------
# Cruzamento Base de Vendas (fibras do portal) x Esteira de Vendas Vivo
# (planilha ANALÍTICO DE VENDAS). Mesmo motor da ferramenta "Validação de
# Vendas Go" — ver fibras/cruzamento.py e fibras/validacao.py.
# ---------------------------------------------------------------------------

class EsteiraImportacao(models.Model):
    """Uma importação da planilha da Esteira de Vendas Vivo (ANALÍTICO)."""
    nome_arquivo = models.CharField(max_length=255, blank=True)
    linhas = models.PositiveIntegerField(default=0)
    colunas_originais = models.PositiveIntegerField(default=0)
    colunas = models.JSONField(default=list, blank=True, help_text='[[coluna solicitada, coluna do arquivo], ...]')
    ausentes = models.JSONField(default=list, blank=True)
    importado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    importado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-importado_em']
        verbose_name = 'Importação da Esteira Vivo'
        verbose_name_plural = 'Importações da Esteira Vivo'

    def __str__(self):
        return f'{self.nome_arquivo} ({self.linhas} linhas)'


class EsteiraVenda(models.Model):
    """Uma linha da Esteira de Vendas Vivo (só a importação mais recente fica guardada)."""
    importacao = models.ForeignKey(EsteiraImportacao, on_delete=models.CASCADE, related_name='vendas')
    linha = models.PositiveIntegerField(default=0, help_text='Posição na planilha.')
    ordem = models.CharField(max_length=40, blank=True, db_index=True)
    adabas = models.CharField(max_length=40, blank=True, db_index=True)
    data_criacao = models.DateField(null=True, blank=True)
    etapa_instalacao = models.CharField(max_length=120, blank=True)
    status_instalacao = models.CharField(max_length=120, blank=True)
    produto = models.CharField(max_length=200, blank=True)
    nome_vendedor = models.CharField(max_length=200, blank=True)
    sla_agenda = models.CharField(max_length=120, blank=True)
    motivo = models.CharField(max_length=255, blank=True)
    dados = models.JSONField(default=dict, blank=True, help_text='Colunas usadas, com os nomes do motor.')
    # Resultado do último cruzamento
    no_escopo = models.BooleanField(default=False)
    fibra = models.ForeignKey(Fibra, on_delete=models.SET_NULL, null=True, blank=True, related_name='esteira_vendas')

    class Meta:
        ordering = ['linha']
        verbose_name = 'Venda da Esteira Vivo'
        verbose_name_plural = 'Vendas da Esteira Vivo'

    def __str__(self):
        return f'ORDEM {self.ordem}'


class FilialAdabas(models.Model):
    """FILIAL da Base de Vendas (PDV do Vivo GO) -> ADABAS da Esteira Vivo, salvo na tela de lojas."""
    filial = models.CharField(max_length=160, unique=True)
    adabas = models.CharField(max_length=40, blank=True, help_text='Vazio = sem correspondência.')
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['filial']
        verbose_name = 'Correspondência FILIAL → ADABAS'
        verbose_name_plural = 'Correspondências FILIAL → ADABAS'

    def __str__(self):
        return f'{self.filial} → {self.adabas or "—"}'


class Cruzamento(models.Model):
    """Uma execução do cruzamento: parâmetros, indicadores e resumo loja/dia."""
    ORIGENS = [('importacao', 'Importação da Esteira'), ('manual', 'Cruzar (tela)'), ('ajuste', 'Ajuste manual'),
               ('lojas', 'Correspondência de lojas')]
    executado_em = models.DateTimeField(auto_now_add=True)
    executado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    origem = models.CharField(max_length=20, choices=ORIGENS, default='manual')
    importacao = models.ForeignKey(EsteiraImportacao, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    ini = models.DateField()
    fim = models.DateField()
    janela = models.PositiveSmallIntegerField(default=1)
    lojas = models.JSONField(default=list, blank=True)
    etapas = models.JSONField(default=list, blank=True)
    kpis = models.JSONField(default=dict, blank=True)
    resumo = models.JSONField(default=dict, blank=True, help_text='{"colunas": [...], "linhas": [[...]]}')

    class Meta:
        ordering = ['-executado_em']
        verbose_name = 'Cruzamento Base de Vendas x Esteira Vivo'
        verbose_name_plural = 'Cruzamentos Base de Vendas x Esteira Vivo'

    def __str__(self):
        return f'Cruzamento {self.executado_em:%d/%m/%Y %H:%M}'


class AcessoFibras(models.Model):
    """O que o SUPERADMIN liberou para uma pessoa em Fibras (chaves de permissoes.FUNCOES)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='acesso_fibras')
    ativo = models.BooleanField(default=True)
    permissoes = models.JSONField(default=list, blank=True)
    observacao = models.CharField(max_length=200, blank=True)
    concedido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Acesso a Fibras'
        verbose_name_plural = 'Acessos a Fibras'

    def __str__(self):
        return f'{self.user} — {len(self.permissoes or [])} função(ões)'


class HistoricoAcessoFibras(models.Model):
    """Quem liberou ou tirou o quê, com os nomes gravados (a pessoa pode sair do portal)."""

    pessoa = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+')
    pessoa_nome = models.CharField(max_length=200)
    antes = models.TextField(blank=True)
    depois = models.TextField(blank=True)
    por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                            related_name='+')
    por_nome = models.CharField(max_length=200, blank=True)
    quando = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-quando']
        verbose_name = 'Histórico de acesso a Fibras'
        verbose_name_plural = 'Histórico de acessos a Fibras'
