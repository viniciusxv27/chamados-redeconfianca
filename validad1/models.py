"""Modelos do módulo Validação D-1."""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class VendaD1(models.Model):
    """Snapshot local de uma venda do dia anterior (D-1) vinda do MySQL."""

    STATUS_PENDENTE = 'pendente'           # ainda não analisada pela Ilha
    STATUS_CONFORMIDADE = 'conformidade'   # venda em conformidade
    STATUS_DIVERGENTE = 'divergente'       # algum tipo de divergência

    STATUS_CHOICES = [
        (STATUS_PENDENTE, 'Pendente análise'),
        (STATUS_CONFORMIDADE, 'Em conformidade'),
        (STATUS_DIVERGENTE, 'Divergente'),
    ]

    # Tipos de divergência (sinalização) -> ação tomada no Vivo GO
    DIV_VENDA_INEXISTENTE = 'venda_inexistente'      # → Excluir venda
    DIV_VALORES_INCORRETOS = 'valores_incorretos'    # → Corrigir receita
    DIV_PLANO_INCORRETO = 'plano_incorreto'          # → Corrigir plano
    DIV_LINHA_INCORRETA = 'linha_incorreta'          # → Alterar n. de acesso
    DIV_CADASTRAL = 'divergencia_cadastral'          # → Alterar cadastro
    DIV_MOVIMENTO_INELEGIVEL = 'movimento_inelegivel'  # → Excluir venda

    TIPO_DIVERGENCIA_CHOICES = [
        (DIV_VENDA_INEXISTENTE, 'Venda inexistente (Excluir venda)'),
        (DIV_VALORES_INCORRETOS, 'Valores incorretos (Corrigir receita)'),
        (DIV_PLANO_INCORRETO, 'Plano incorreto (Corrigir plano)'),
        (DIV_LINHA_INCORRETA, 'Linha incorreta (Alterar n. de acesso)'),
        (DIV_CADASTRAL, 'Divergência cadastral (Alterar cadastro)'),
        (DIV_MOVIMENTO_INELEGIVEL, 'Movimento inelegível (Excluir venda)'),
    ]

    # Penalidades (a Ilha sinaliza)
    PEN_NENHUMA = 'nenhuma'
    PEN_LEVE = 'leve'
    PEN_MEDIA = 'media'
    PEN_GRAVE = 'grave'
    PENALIDADE_CHOICES = [
        (PEN_NENHUMA, 'Nenhuma'),
        (PEN_LEVE, 'Leve'),
        (PEN_MEDIA, 'Média'),
        (PEN_GRAVE, 'Grave'),
    ]

    # De acordo (gerente)
    ACORDO_PENDENTE = 'pendente'
    ACORDO_DE_ACORDO = 'de_acordo'
    ACORDO_CONTESTADO = 'contestado'
    ACORDO_EXPIRADO = 'expirado'
    ACORDO_CHOICES = [
        (ACORDO_PENDENTE, 'Aguardando'),
        (ACORDO_DE_ACORDO, 'De acordo'),
        (ACORDO_CONTESTADO, 'Contestado'),
        (ACORDO_EXPIRADO, 'Expirado (48h)'),
    ]

    numero_da_venda = models.CharField('Número da venda', max_length=60)
    produto = models.CharField('Produto / plano', max_length=200, blank=True)
    valor = models.DecimalField('Valor', max_digits=12, decimal_places=2, default=0)
    cpf = models.CharField('CPF do cliente', max_length=20, blank=True)
    numero_acesso = models.CharField('Número de acesso', max_length=60, blank=True)
    data_da_venda = models.DateField('Data da venda', null=True, blank=True)
    pilar = models.CharField('Pilar', max_length=40, blank=True)
    vendedor = models.CharField('Vendedor', max_length=200, blank=True, db_index=True)
    pdv = models.CharField('Loja (PDV)', max_length=120, blank=True, db_index=True)
    servicos = models.CharField('Serviços', max_length=200, blank=True)

    # Status / sinalização
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDENTE, db_index=True)
    tipo_divergencia = models.CharField(
        max_length=30, choices=TIPO_DIVERGENCIA_CHOICES, blank=True,
    )
    penalidade = models.CharField(max_length=10, choices=PENALIDADE_CHOICES, default=PEN_NENHUMA)
    acao_realizada_no_go = models.BooleanField('Alteração feita no Vivo GO', default=False)
    observacao = models.TextField('Observação da Ilha', blank=True)
    marcado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vendas_d1_marcadas',
    )
    marcado_em = models.DateTimeField(null=True, blank=True)

    # Acordo do gerente
    acordo_status = models.CharField(max_length=15, choices=ACORDO_CHOICES, default=ACORDO_PENDENTE, db_index=True)
    acordo_respondido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vendas_d1_respondidas',
    )
    acordo_respondido_em = models.DateTimeField(null=True, blank=True)
    acordo_deadline = models.DateTimeField(null=True, blank=True)

    # Duplicidade
    is_duplicate = models.BooleanField(default=False)
    duplicate_of = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='duplicates',
    )

    # Bookkeeping
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Venda D-1'
        verbose_name_plural = 'Vendas D-1'
        ordering = ['-data_da_venda', '-id']
        # Uma venda pode aparecer mais de uma vez (duplicada); usamos unique_together
        # apenas para a "linha principal" via business logic, não constraint DB.
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['acordo_status']),
            models.Index(fields=['data_da_venda']),
        ]

    def __str__(self):
        return f"D-1 {self.numero_da_venda} ({self.data_da_venda})"

    def set_divergente(self, *, tipo: str, penalidade: str, observacao: str, por_usuario):
        self.status = self.STATUS_DIVERGENTE
        self.tipo_divergencia = tipo
        self.penalidade = penalidade
        self.observacao = observacao
        self.marcado_por = por_usuario
        self.marcado_em = timezone.now()
        # Inicia janela de 48h para o gerente responder
        self.acordo_status = self.ACORDO_PENDENTE
        self.acordo_deadline = timezone.now() + timedelta(hours=48)
        self.save()

    def set_conformidade(self, *, por_usuario):
        self.status = self.STATUS_CONFORMIDADE
        self.tipo_divergencia = ''
        self.penalidade = self.PEN_NENHUMA
        self.marcado_por = por_usuario
        self.marcado_em = timezone.now()
        self.acordo_status = self.ACORDO_PENDENTE
        self.acordo_deadline = None
        self.save()


class VendaD1Contestacao(models.Model):
    """Quando o gerente contesta uma divergência, abre uma 'tratativa' interna."""
    STATUS_ABERTA = 'aberta'
    STATUS_PROCEDENTE = 'procedente'
    STATUS_IMPROCEDENTE = 'improcedente'
    STATUS_CHOICES = [
        (STATUS_ABERTA, 'Aberta'),
        (STATUS_PROCEDENTE, 'Procedente (ajustar no GO)'),
        (STATUS_IMPROCEDENTE, 'Improcedente'),
    ]

    venda = models.ForeignKey(VendaD1, on_delete=models.CASCADE, related_name='contestacoes')
    aberto_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='contestacoes_d1_abertas',
    )
    motivo = models.TextField('Motivo da contestação')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_ABERTA)
    resposta = models.TextField('Resposta da Ilha', blank=True)
    respondido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    aberto_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    # Última vez que o autor da contestação (vendedor/gerente) abriu a tratativa.
    # Usado para destacar visualmente o card quando há resposta nova da Ilha.
    last_opener_view_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-aberto_em']

    def __str__(self):
        return f"Contestação {self.id} - Venda {self.venda.numero_da_venda}"

    @property
    def has_unread_reply_for_opener(self) -> bool:
        """True quando há mensagem de outra pessoa que não o autor após a
        última visita do autor (vendedor/gerente)."""
        ref = self.last_opener_view_at
        qs = self.mensagens.all()
        if self.aberto_por_id:
            qs = qs.exclude(autor_id=self.aberto_por_id)
        if ref:
            qs = qs.filter(criado_em__gt=ref)
        return qs.exists()


class VendaD1ChatMessage(models.Model):
    """Mensagens dentro do 'chat de contestação' (Ilha ↔ gerente)."""
    contestacao = models.ForeignKey(
        VendaD1Contestacao, on_delete=models.CASCADE, related_name='mensagens',
    )
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='+',
    )
    texto = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['criado_em']

    def __str__(self):
        return f"Msg {self.id} ({self.contestacao_id})"


def _vd1_attachment_upload_to(instance, filename):
    return f'validad1/contestacoes/{instance.mensagem.contestacao_id}/{filename}'


class VendaD1ChatAttachment(models.Model):
    """Anexos (imagens, vídeos, documentos) vinculados a uma mensagem do chat."""

    KIND_IMAGE = 'image'
    KIND_VIDEO = 'video'
    KIND_FILE = 'file'

    mensagem = models.ForeignKey(
        VendaD1ChatMessage, on_delete=models.CASCADE, related_name='anexos',
    )
    arquivo = models.FileField(upload_to=_vd1_attachment_upload_to)
    nome_original = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    tamanho = models.PositiveIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']

    @property
    def kind(self) -> str:
        ct = (self.content_type or '').lower()
        if ct.startswith('image/'):
            return self.KIND_IMAGE
        if ct.startswith('video/'):
            return self.KIND_VIDEO
        return self.KIND_FILE

    @property
    def is_image(self) -> bool:
        return self.kind == self.KIND_IMAGE

    @property
    def is_video(self) -> bool:
        return self.kind == self.KIND_VIDEO

    def __str__(self):
        return self.nome_original or self.arquivo.name
