from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal

from core.storage import get_media_storage


class TipoBase(models.TextChoices):
    """De qual planilha veio a linha.

    As duas convivem na mesma tabela porque o fluxo de contestação (abrir,
    gestor aprova, gerente confirma, pagar) é idêntico nos dois casos — o que
    muda é só a origem e o motivo. Separar em tabelas obrigaria a duplicar
    todo esse fluxo.

    Toda consulta do fluxo antigo precisa filtrar por EXCLUSAO: sem isso, uma
    linha de pagamento apareceria na tela de exclusões.
    """

    EXCLUSAO = 'exclusao', 'BASE_EXCLUSAO'
    PAGAMENTO = 'pagamento', 'BASE_PAGAMENTO (valores)'


class ExclusionSyncBatch(models.Model):
    """Lote de importação da planilha BASE_EXCLUSAO.

    Cada clique em "Sincronizar Planilha" gera um novo lote. Os registros
    importados ficam vinculados ao lote, permitindo:
      - Exibir apenas as vendas da planilha mais recente nas telas de
        contestação (lotes antigos permanecem no banco apenas para auditoria).
      - Reabrir todas as lojas para uma nova rodada de contestação, já que
        as contestações antigas continuam vinculadas aos registros do lote
        anterior.
    """

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='exclusion_sync_batches',
        verbose_name='Sincronizado por',
    )
    record_count = models.PositiveIntegerField(default=0, verbose_name='Registros importados')
    notes = models.TextField(blank=True, default='', verbose_name='Observações')
    # Lotes antigos são todos de exclusão; o default preserva isso.
    record_type = models.CharField(
        max_length=12, choices=TipoBase.choices, default=TipoBase.EXCLUSAO,
        db_index=True, verbose_name='Origem')

    class Meta:
        verbose_name = 'Lote de Sincronização de Exclusões'
        verbose_name_plural = 'Lotes de Sincronização de Exclusões'
        ordering = ['-created_at']

    def __str__(self):
        return f'Lote #{self.pk} – {self.record_count} registros ({self.created_at:%d/%m/%Y %H:%M})'


class ExclusionRecord(models.Model):
    """Registro importado da planilha BASE_EXCLUSAO."""
    sync_batch = models.ForeignKey(
        ExclusionSyncBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='records',
        verbose_name='Lote de Sincronização',
    )
    filial = models.CharField(max_length=100, verbose_name='Filial')
    vendedor = models.CharField(max_length=200, verbose_name='Vendedor')
    receita = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Receita')
    pilar = models.CharField(max_length=50, verbose_name='Pilar')
    gerente = models.CharField(max_length=200, blank=True, default='', verbose_name='Gerente')
    coordenacao = models.CharField(max_length=200, blank=True, default='', verbose_name='Coordenação')
    numero_venda = models.CharField(max_length=100, blank=True, default='', verbose_name='Nº da Venda')
    data_venda = models.CharField(max_length=50, blank=True, default='', verbose_name='Data da Venda')
    nome_cliente = models.CharField(max_length=300, blank=True, default='', verbose_name='Nome Cliente')
    cpf_cnpj = models.CharField(max_length=30, blank=True, default='', verbose_name='CPF/CNPJ')
    plano_produto = models.CharField(max_length=300, blank=True, default='', verbose_name='Plano/Produto')
    imei = models.CharField(max_length=120, blank=True, default='', verbose_name='IMEI')
    numero_acesso = models.CharField(max_length=100, blank=True, default='', verbose_name='Número Acesso')
    observacao = models.TextField(blank=True, default='', verbose_name='Observação')
    record_type = models.CharField(
        max_length=12, choices=TipoBase.choices, default=TipoBase.EXCLUSAO,
        db_index=True, verbose_name='Origem')
    # Só a base de pagamento traz este valor. Nulo não é zero: "não veio na
    # planilha" e "recebeu zero" são coisas diferentes, e tratar um como o
    # outro inventaria divergência do tamanho da receita inteira.
    recebido = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Recebido')
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name='Importado em')

    class Meta:
        verbose_name = 'Registro de Exclusão'
        verbose_name_plural = 'Registros de Exclusão'
        ordering = ['-imported_at']
        indexes = [
            models.Index(fields=['filial']),
            models.Index(fields=['vendedor']),
            models.Index(fields=['pilar']),
            models.Index(fields=['sync_batch']),
            models.Index(fields=['record_type', 'sync_batch']),
        ]

    def __str__(self):
        return f'{self.vendedor} – {self.pilar} R${self.receita} ({self.filial})'

    @property
    def divergencia(self):
        """Receita menos recebido. ``None`` quando não há recebido informado.

        Positivo = recebeu menos do que a receita da venda, que é o caso que
        interessa contestar. Negativo aparece na tela como está: se recebeu a
        mais, quem confere precisa ver isso e não um zero maquiado.
        """
        if self.recebido is None:
            return None
        return (self.receita or Decimal('0')) - self.recebido

    @property
    def tem_divergencia(self):
        divergencia = self.divergencia
        return divergencia is not None and divergencia != 0


class Contestation(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('accepted', 'Aprovada pelo Gestor'),
        ('rejected', 'Rejeitada'),
        ('confirmed', 'Confirmada pelo Gerente'),
        ('denied', 'Negada pelo Gerente'),
    ]
    PAYMENT_CHOICES = [
        ('not_applicable', 'N/A'),
        ('pending_payment', 'Aguardando Pagamento'),
        ('paid', 'Pago'),
    ]
    APPROVAL_MODE_CHOICES = [
        ('approved', 'Aprovar'),
        ('approved_and_contested', 'Aprovar e Contestar'),
    ]

    exclusion = models.ForeignKey(
        ExclusionRecord, on_delete=models.CASCADE,
        related_name='contestations', verbose_name='Registro de Exclusão'
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='contestations_created', verbose_name='Solicitante'
    )
    reason = models.TextField(verbose_name='Motivo da Contestação')
    attachment_wrong = models.BooleanField(default=False, verbose_name='Anexo veio errado')
    attachment = models.FileField(
        upload_to='contestacoes/%Y/%m/', storage=get_media_storage(),
        blank=True, null=True, verbose_name='Anexo/Evidência'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='Status')
    payment_status = models.CharField(
        max_length=20, choices=PAYMENT_CHOICES, default='not_applicable',
        verbose_name='Status do Pagamento'
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='contestations_reviewed',
        verbose_name='Analisado por'
    )
    review_notes = models.TextField(blank=True, default='', verbose_name='Observações da Análise')
    review_attachment = models.FileField(
        upload_to='contestacoes/gestor/%Y/%m/', storage=get_media_storage(),
        blank=True, null=True, verbose_name='Anexo do Gestor'
    )
    approval_mode = models.CharField(
        max_length=30,
        choices=APPROVAL_MODE_CHOICES,
        default='approved',
        verbose_name='Modo de Aprovação'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='Data da Análise')
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='contestations_confirmed',
        verbose_name='Confirmado por'
    )
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name='Data da Confirmação')
    confirmation_notes = models.TextField(blank=True, default='', verbose_name='Observações da Confirmação')
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name='Data do Pagamento')
    sale_value_was_edited = models.BooleanField(default=False, verbose_name='Venda editada')
    sale_value_original = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Valor original da venda'
    )
    sale_value_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contestations_sale_value_edited',
        verbose_name='Venda editada por'
    )
    sale_value_edited_at = models.DateTimeField(null=True, blank=True, verbose_name='Data da edição da venda')
    # Contestação feita pela equipe (17/11/2026): o CN responde as próprias
    # vendas e só o gerente envia. ``requester`` continua sendo o dono da venda
    # (o CN); estes dois dizem quem escreveu o motivo/anexou a evidência — o
    # gerente pode fazer pelo CN — e quem apertou o botão de enviar. Nulos nas
    # contestações de antes, quando quem preenchia e quem enviava era o próprio
    # solicitante.
    preenchido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='contestations_preenchidas', verbose_name='Preenchida por')
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='contestations_enviadas', verbose_name='Enviada por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    class Meta:
        verbose_name = 'Contestação'
        verbose_name_plural = 'Contestações'
        ordering = ['-created_at']

    def __str__(self):
        return f'Contestação #{self.pk} – {self.exclusion.vendedor} ({self.get_status_display()})'

    def approve(self, reviewer, notes='', approval_mode='approved', attachment=None):
        """Gestor aprova e envia direto para pagamento."""
        self.status = 'confirmed'
        self.payment_status = 'pending_payment'
        self.reviewed_by = reviewer
        self.review_notes = notes
        self.approval_mode = approval_mode
        if attachment:
            self.review_attachment = attachment
        now = timezone.now()
        self.reviewed_at = now
        self.confirmed_by = reviewer
        self.confirmed_at = now
        self.save()

    def reject(self, reviewer, notes='', attachment=None):
        """Gestor rejeita — aguarda de acordo do gerente."""
        self.status = 'rejected'
        self.reviewed_by = reviewer
        self.review_notes = notes
        if attachment:
            self.review_attachment = attachment
        self.reviewed_at = timezone.now()
        self.save()

    def confirm(self, requester, notes=''):
        """Gerente dá de acordo após rejeição do gestor."""
        if self.status == 'accepted':
            self.status = 'confirmed'
            self.payment_status = 'pending_payment'
        # Se rejeitada, mantém 'rejected' mas registra o de acordo
        self.confirmed_by = requester
        self.confirmation_notes = notes
        self.confirmed_at = timezone.now()
        self.save()

    def deny_confirmation(self, requester, notes=''):
        """Gerente nega/discorda da decisão do gestor."""
        self.status = 'denied'
        self.confirmed_by = requester
        self.confirmation_notes = notes
        self.confirmed_at = timezone.now()
        self.save()

    def mark_paid(self, reviewer):
        self.payment_status = 'paid'
        self.reviewed_by = reviewer
        self.paid_at = timezone.now()
        self.save()


class ContestationHistory(models.Model):
    """Histórico de ações realizadas em contestações."""
    ACTION_CHOICES = [
        ('created', 'Contestação Criada'),
        ('approved', 'Aprovada pelo Gestor'),
        ('approved_and_contested', 'Aprovada e Contestada pelo Gestor'),
        ('rejected', 'Rejeitada pelo Gestor'),
        ('confirmed', 'Confirmada pelo Gerente'),
        ('denied', 'Negada pelo Gerente'),
        ('paid', 'Marcada como Paga'),
        ('synced', 'Planilha Sincronizada'),
        # Ação separada de propósito: a janela de contestação das exclusões é
        # calculada a partir do último 'synced', e sincronizar a base de
        # pagamento não pode reabrir aquele prazo.
        ('synced_pagamento', 'Base de Pagamento Sincronizada'),
        # Parte de cada CN no ciclo (sem contestação ligada: a venda ainda não
        # virou contestação). ``user`` é quem fez de verdade; ``extra_data``
        # guarda para quem (o CN dono da venda) e a venda.
        ('nao_contesta', 'Venda respondida como Não (com justificativa)'),
        ('nao_desfeito', 'Resposta Não desfeita'),
        ('cn_pronto', 'Parte do CN marcada como pronta'),
        ('cn_reaberto', 'Parte do CN reaberta'),
        # Fluxo invertido (23/11/2026): o gerente responde, o CN dá o de acordo
        # só nas vendas que o gerente marcou Não (24 h, depois é automático).
        ('cn_de_acordo', 'CN de acordo com o Não do gerente'),
        ('cn_de_acordo_auto', 'De acordo automático por prazo expirado'),
        ('cn_discorda', 'CN discordou do Não do gerente'),
        ('nao_mantido', 'Gerente manteve o Não depois da discordância do CN'),
    ]

    contestation = models.ForeignKey(
        Contestation, on_delete=models.CASCADE,
        related_name='history', verbose_name='Contestação',
        null=True, blank=True,
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES, verbose_name='Ação')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='contestation_actions',
        verbose_name='Usuário'
    )
    notes = models.TextField(blank=True, default='', verbose_name='Observações')
    extra_data = models.JSONField(blank=True, default=dict, verbose_name='Dados Extras')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data')

    class Meta:
        verbose_name = 'Histórico de Contestação'
        verbose_name_plural = 'Históricos de Contestações'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_action_display()} – {self.user} ({self.created_at:%d/%m/%Y %H:%M})'


class ContestationCartDraft(models.Model):
    """Rascunho persistente do carrinho de contestacao por usuario e registro."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='contestation_cart_drafts',
        verbose_name='Usuario',
    )
    exclusion = models.ForeignKey(
        ExclusionRecord,
        on_delete=models.CASCADE,
        related_name='contestation_cart_drafts',
        verbose_name='Registro de Exclusao',
    )
    reason = models.TextField(blank=True, default='', verbose_name='Motivo')
    attachment = models.FileField(
        upload_to='contestacoes/rascunhos/%Y/%m/',
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name='Anexo de Rascunho',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    class Meta:
        verbose_name = 'Rascunho de Carrinho de Contestacao'
        verbose_name_plural = 'Rascunhos de Carrinho de Contestacao'
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'exclusion'], name='uniq_contestation_cart_draft_user_exclusion'),
        ]
        indexes = [
            models.Index(fields=['user', 'updated_at']),
        ]

    def __str__(self):
        return f'Rascunho #{self.pk} - {self.user_id}/{self.exclusion_id}'


class LiberacaoContestacao(models.Model):
    """Reabre a contestação de UM setor por um prazo definido.

    A janela normal é global: três dias contados da última sincronização. Ela
    resolve o caso comum, mas não o caso real de uma loja que perdeu o prazo —
    até aqui a única saída era sincronizar a planilha de novo, o que reabre
    tudo para todo mundo e mistura rodada nova com rodada velha.

    Esta liberação é cirúrgica: vale para um setor, até uma data e hora, e
    aparece na tela de quem foi liberado com o prazo real.
    """

    setor = models.ForeignKey(
        'users.Sector', on_delete=models.CASCADE, related_name='liberacoes_contestacao',
        verbose_name='Setor liberado')
    prazo = models.DateTimeField(verbose_name='Liberado até')
    motivo = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Motivo',
        help_text='Fica registrado para quem for auditar a rodada depois.')
    ativa = models.BooleanField(default=True, verbose_name='Ativa')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='liberacoes_contestacao_criadas', verbose_name='Liberado por')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Liberação de contestação'
        verbose_name_plural = 'Liberações de contestação'
        ordering = ['-prazo']
        indexes = [models.Index(fields=['setor', 'prazo'])]

    def __str__(self):
        return f'{self.setor} até {self.prazo:%d/%m/%Y %H:%M}'

    @property
    def vigente(self):
        return self.ativa and self.prazo > timezone.now()

    @classmethod
    def para_usuario(cls, user):
        """A liberação vigente que alcança este usuário, ou None.

        Olha todos os setores da pessoa — principal e vinculados —, porque
        quem responde por duas lojas precisa da liberação valendo nas duas.
        """
        if not (user and getattr(user, 'is_authenticated', False)):
            return None
        ids = set(user.sectors.values_list('id', flat=True))
        if getattr(user, 'sector_id', None):
            ids.add(user.sector_id)
        if not ids:
            return None
        return (cls.objects.filter(setor_id__in=ids, ativa=True,
                                   prazo__gt=timezone.now())
                .select_related('setor').order_by('-prazo').first())


class DestinatarioResumoCiclo(models.Model):
    """Quem recebe no WhatsApp o resumo do ciclo. O SUPERADMIN escolhe.

    Nasce vazia de propósito: sem ninguém aqui, nada sai — nem o fechamento do
    ciclo é "gasto" com um envio para ninguém.
    """

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='destinatario_resumo_contestacao', verbose_name='Usuário')
    incluido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Incluído por')
    incluido_em = models.DateTimeField(auto_now_add=True, verbose_name='Incluído em')

    class Meta:
        verbose_name = 'Destinatário do resumo do ciclo'
        verbose_name_plural = 'Destinatários do resumo do ciclo'

    def __str__(self):
        return f'{self.usuario}'


class EnvioResumoCiclo(models.Model):
    """O resumo de um ciclo (lote da BASE_EXCLUSAO): existe no máximo um por lote.

    O ``OneToOneField`` é a trava contra envio duplo: duas lojas dando o de
    acordo ao mesmo tempo, em workers diferentes, tentam criar a mesma linha e
    o banco deixa passar só uma.
    """

    AUTOMATICO = 'automatico'
    MANUAL = 'manual'
    ORIGENS = [(AUTOMATICO, 'Automático (ciclo fechou)'), (MANUAL, 'Manual (SUPERADMIN)')]

    lote = models.OneToOneField(
        ExclusionSyncBatch, on_delete=models.CASCADE,
        related_name='envio_resumo', verbose_name='Ciclo (lote)')
    origem = models.CharField(max_length=12, choices=ORIGENS, default=AUTOMATICO, verbose_name='Origem')
    disparado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Disparado por')
    ciclo_fechado = models.BooleanField(
        default=True, verbose_name='Ciclo estava fechado',
        help_text='Falso quando o SUPERADMIN mandou antes de todas as lojas terminarem.')
    # Foto do que foi mandado: a contestação continua mudando depois do envio.
    pdf = models.FileField(
        upload_to='contestacoes/resumos/%Y/%m/', storage=get_media_storage(),
        blank=True, null=True, verbose_name='PDF do resumo')
    pdf_gerado_em = models.DateTimeField(null=True, blank=True, verbose_name='PDF gerado em')
    texto = models.TextField(blank=True, default='', verbose_name='Mensagem')
    totais = models.JSONField(blank=True, default=dict, verbose_name='Totais')
    erro = models.CharField(max_length=500, blank=True, default='', verbose_name='Erro ao preparar')
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    concluido_em = models.DateTimeField(null=True, blank=True, verbose_name='Concluído em')

    class Meta:
        verbose_name = 'Envio do resumo do ciclo'
        verbose_name_plural = 'Envios do resumo do ciclo'
        ordering = ['-criado_em']

    def __str__(self):
        return f'Resumo do lote {self.lote_id} ({self.criado_em:%d/%m/%Y %H:%M})'


class EnvioResumoDestinatario(models.Model):
    """Uma pessoa dentro de um envio.

    Só fica ``enviado`` quem a integração confirmou. O UPDATE condicional de
    ``pendente`` para ``enviando`` decide quem manda, então a thread do fechamento,
    o agendador e o botão podem se cruzar sem a pessoa receber duas vezes.
    """

    PENDENTE = 'pendente'
    ENVIANDO = 'enviando'
    ENVIADO = 'enviado'
    FALHOU = 'falhou'
    SEM_TELEFONE = 'sem_telefone'
    SITUACOES = [
        (PENDENTE, 'Na fila'),
        (ENVIANDO, 'Enviando'),
        (ENVIADO, 'Enviado'),
        (FALHOU, 'Falhou'),
        (SEM_TELEFONE, 'Sem telefone'),
    ]

    envio = models.ForeignKey(EnvioResumoCiclo, on_delete=models.CASCADE, related_name='linhas', verbose_name='Envio')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='resumos_contestacao_recebidos', verbose_name='Usuário')
    nome = models.CharField(max_length=200, blank=True, default='', verbose_name='Nome')
    numero = models.CharField(max_length=30, blank=True, default='', verbose_name='Número usado')
    situacao = models.CharField(max_length=12, choices=SITUACOES, default=PENDENTE, db_index=True, verbose_name='Situação')
    detalhe = models.CharField(max_length=300, blank=True, default='', verbose_name='Detalhe')
    tentativas = models.PositiveSmallIntegerField(default=0, verbose_name='Tentativas')
    enviando_desde = models.DateTimeField(null=True, blank=True, verbose_name='Enviando desde')
    enviado_em = models.DateTimeField(null=True, blank=True, verbose_name='Enviado em')
    # [{'em': iso, 'situacao': ..., 'detalhe': ...}]: a linha é reaproveitada no reenvio
    # e sem isto a falha de ontem sumiria do histórico.
    historico = models.JSONField(blank=True, default=list, verbose_name='Tentativas anteriores')

    class Meta:
        verbose_name = 'Destinatário de um envio do resumo'
        verbose_name_plural = 'Destinatários de um envio do resumo'
        ordering = ['nome']
        constraints = [
            models.UniqueConstraint(fields=['envio', 'usuario'], name='uniq_envio_resumo_usuario'),
        ]

    def __str__(self):
        return f'{self.nome} – {self.get_situacao_display()}'


class RespostaNaoContesta(models.Model):
    """O "Não" de uma venda: o gerente diz que ela não vai ser contestada.

    Desde 23/11/2026 quem responde é o gerente; o CN dono da venda só dá o de
    acordo nas vendas marcadas Não, em até 24 h (``prazo_cn``). Passou o prazo,
    o de acordo é automático ("De acordo automático por prazo expirado"). Se o
    CN discorda, a venda volta para o gerente, que contesta (vira Sim) ou
    mantém o Não com uma resposta.

    Antes o botão "Não" só escondia a linha no navegador de quem clicou — não
    ficava registro nenhum e ninguém precisava explicar. Agora a resposta é da
    venda (uma por venda, valendo para a loja toda) e a justificativa é
    obrigatória. ``cn`` é o dono da venda (o vendedor, quando tem cadastro no
    portal); ``preenchido_por`` é quem respondeu de verdade — o gerente pode
    responder pelo CN.
    """

    exclusion = models.OneToOneField(
        ExclusionRecord, on_delete=models.CASCADE, related_name='resposta_nao',
        verbose_name='Venda')
    justificativa = models.TextField(verbose_name='Justificativa')
    cn = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='respostas_nao_contestacao', verbose_name='CN (dono da venda)')
    preenchido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='respostas_nao_preenchidas', verbose_name='Respondido por')
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    atualizado_em = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    # O de acordo do CN (fluxo de 23/11/2026).
    AGUARDANDO = 'aguardando'
    DE_ACORDO = 'de_acordo'
    AUTOMATICO = 'automatico'
    DISCORDA = 'discorda'
    MANTIDO = 'mantido'
    SEM_CN = 'sem_cn'
    SITUACOES_CN = [
        (AGUARDANDO, 'Aguardando o de acordo do CN'),
        (DE_ACORDO, 'CN de acordo'),
        (AUTOMATICO, 'De acordo automático por prazo expirado'),
        (DISCORDA, 'CN discordou — com o gerente'),
        (MANTIDO, 'Gerente manteve o Não'),
        (SEM_CN, 'Venda sem CN no portal'),
    ]
    situacao_cn = models.CharField(
        max_length=12, choices=SITUACOES_CN, default=AGUARDANDO, db_index=True,
        verbose_name='De acordo do CN')
    prazo_cn = models.DateTimeField(null=True, blank=True, verbose_name='Prazo do CN')
    cn_respondido_em = models.DateTimeField(null=True, blank=True, verbose_name='CN respondeu em')
    cn_respondido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='respostas_nao_de_acordo', verbose_name='De acordo dado por')
    cn_motivo = models.TextField(blank=True, default='', verbose_name='Por que o CN discorda')
    gerente_resposta = models.TextField(blank=True, default='',
                                        verbose_name='Resposta do gerente à discordância')
    mantido_em = models.DateTimeField(null=True, blank=True, verbose_name='Não mantido em')
    mantido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='respostas_nao_mantidas', verbose_name='Não mantido por')

    @property
    def aguardando_cn(self):
        return self.situacao_cn == self.AGUARDANDO

    @property
    def de_acordo_automatico(self):
        return self.situacao_cn == self.AUTOMATICO

    @property
    def com_o_gerente(self):
        return self.situacao_cn == self.DISCORDA

    class Meta:
        verbose_name = 'Venda respondida como Não'
        verbose_name_plural = 'Vendas respondidas como Não'
        ordering = ['-atualizado_em']

    def __str__(self):
        return f'Não – venda {self.exclusion_id}'


class ParteDoCN(models.Model):
    """O CN terminou a parte dele no ciclo ("pronto").

    Um por vendedor e loja dentro do lote. O vendedor é identificado como vem
    na planilha (``filial`` + ``vendedor``), porque é isso que a base traz; o
    ``cn`` é o usuário do portal com aquele nome, quando existe. O gerente só
    consegue enviar as contestações da loja quando todos os CNs ativos com
    venda na base estão prontos — e "pronto" só vale enquanto todas as vendas
    dele continuam respondidas (Sim com motivo e evidência, ou Não com
    justificativa).
    """

    lote = models.ForeignKey(
        ExclusionSyncBatch, on_delete=models.CASCADE, related_name='partes_dos_cns',
        verbose_name='Ciclo (lote)')
    filial = models.CharField(max_length=100, verbose_name='Filial')
    vendedor = models.CharField(max_length=200, verbose_name='Vendedor (como na planilha)')
    cn = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='partes_contestacao', verbose_name='CN')
    marcado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='partes_contestacao_marcadas', verbose_name='Marcado por')
    marcado_em = models.DateTimeField(auto_now=True, verbose_name='Marcado em')

    class Meta:
        verbose_name = 'Parte do CN no ciclo'
        verbose_name_plural = 'Partes dos CNs no ciclo'
        constraints = [
            models.UniqueConstraint(fields=['lote', 'filial', 'vendedor'], name='uniq_parte_cn_lote_filial_vendedor'),
        ]

    def __str__(self):
        return f'{self.vendedor} ({self.filial}) – lote {self.lote_id}'


class DecisaoNoCarrinho(models.Model):
    """Carrinho do gestor em /contestacao/gerenciar/: a decisão marcada numa contestação pendente.

    Fica guardada (no servidor, por gestor) até ele aplicar; aplicar faz o mesmo que os botões
    Aprovar / Aprovar e Contestar / Rejeitar de cada contestação e tira a linha do carrinho.
    """
    APROVAR, APROVAR_E_CONTESTAR, REJEITAR = 'approved', 'approved_and_contested', 'rejected'
    DECISOES = [
        (APROVAR, 'De acordo (aprovar)'),
        (APROVAR_E_CONTESTAR, 'Aprovar e contestar'),
        (REJEITAR, 'Negar (rejeitar)'),
    ]

    gestor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+',
                               verbose_name='Gestor')
    contestation = models.ForeignKey(Contestation, on_delete=models.CASCADE, related_name='no_carrinho',
                                     verbose_name='Contestação')
    decisao = models.CharField(max_length=24, choices=DECISOES, verbose_name='Decisão')
    observacao = models.TextField(blank=True, default='', verbose_name='Observação / parecer')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['criado_em', 'id']
        unique_together = [('gestor', 'contestation')]
        verbose_name = 'Decisão no carrinho do gestor'
        verbose_name_plural = 'Decisões no carrinho do gestor'

    def __str__(self):
        return f'#{self.contestation_id}: {self.get_decisao_display()}'
