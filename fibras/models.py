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
