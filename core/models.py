from django.db import models
from django.conf import settings
from django.utils import timezone

User = settings.AUTH_USER_MODEL


class SystemLog(models.Model):
    ACTION_TYPES = [
        ('USER_LOGIN', 'Login de Usuário'),
        ('USER_LOGOUT', 'Logout de Usuário'),
        ('TICKET_CREATE', 'Criação de Chamado'),
        ('TICKET_UPDATE', 'Atualização de Chamado'),
        ('CS_CHANGE', 'Alteração de C$'),
        ('PRIZE_REDEEM', 'Resgate de Prêmio'),
        ('COMMUNICATION_SEND', 'Envio de Comunicado'),
        ('USER_CREATE', 'Criação de Usuário'),
        ('USER_UPDATE', 'Atualização de Usuário'),
        ('ADMIN_ACTION', 'Ação Administrativa'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Usuário"
    )
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES, verbose_name="Tipo de Ação")
    description = models.TextField(verbose_name="Descrição")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="Endereço IP")
    user_agent = models.TextField(blank=True, verbose_name="User Agent")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data")
    
    class Meta:
        verbose_name = "Log do Sistema"
        verbose_name_plural = "Logs do Sistema"
        ordering = ['-created_at']
    
    def __str__(self):
        user_name = self.user.full_name if self.user else "Sistema"
        return f"{user_name} - {self.action_type}"


class Tutorial(models.Model):
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(verbose_name="Descrição")
    pdf_file = models.FileField(upload_to='tutorials/', verbose_name="Arquivo PDF")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        verbose_name="Criado por"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data de Criação")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    order = models.IntegerField(default=0, verbose_name="Ordem")

    class Meta:
        verbose_name = "Tutorial"
        verbose_name_plural = "Tutoriais"
        ordering = ['order', 'title']

    def __str__(self):
        return self.title


class Report(models.Model):
    REPORT_TYPES = [
        ('HARASSMENT', 'Assédio/Bullying'),
        ('INAPPROPRIATE_CONTENT', 'Conteúdo Inadequado'),
        ('SPAM', 'Spam'),
        ('FAKE_INFO', 'Informação Falsa'),
        ('DISCRIMINATION', 'Discriminação'),
        ('VIOLENCE', 'Violência/Ameaças'),
        ('OTHER', 'Outro'),
    ]
    
    STATUS_CHOICES = [
        ('PENDING', 'Pendente'),
        ('UNDER_REVIEW', 'Em Análise'),
        ('RESOLVED', 'Resolvida'),
        ('DISMISSED', 'Descartada'),
    ]
    
    PRIORITY_CHOICES = [
        ('LOW', 'Baixa'),
        ('MEDIUM', 'Média'),
        ('HIGH', 'Alta'),
        ('CRITICAL', 'Crítica'),
    ]
    
    # Quem está fazendo a denúncia
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports_made', verbose_name="Denunciante")
    
    # Contra quem é a denúncia (opcional)
    reported_user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='reports_received', verbose_name="Usuário Denunciado")
    
    # Detalhes da denúncia
    report_type = models.CharField(max_length=30, choices=REPORT_TYPES, verbose_name="Tipo de Denúncia")
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(verbose_name="Descrição")
    evidence = models.FileField(upload_to='reports/evidence/', blank=True, null=True, verbose_name="Evidência")
    
    # Metadados
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING', verbose_name="Status")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM', verbose_name="Prioridade")
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")
    
    # Quem está analisando
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_reports', verbose_name="Responsável")
    
    # Resposta/Resolução
    admin_notes = models.TextField(blank=True, verbose_name="Notas do Administrador")
    resolution = models.TextField(blank=True, verbose_name="Resolução")
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="Resolvido em")
    
    # Informações adicionais
    is_anonymous = models.BooleanField(default=False, verbose_name="Denúncia Anônima")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP Address")
    
    class Meta:
        verbose_name = "Denúncia"
        verbose_name_plural = "Denúncias"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['reporter', 'created_at']),
            models.Index(fields=['reported_user', 'created_at']),
        ]
    
    def __str__(self):
        target = f" contra {self.reported_user.full_name}" if self.reported_user else ""
        return f"{self.get_report_type_display()}{target} - {self.reporter.full_name}"
    
    def save(self, *args, **kwargs):
        if self.status == 'RESOLVED' and not self.resolved_at:
            self.resolved_at = timezone.now()
        super().save(*args, **kwargs)
    
    @property
    def is_urgent(self):
        return self.priority in ['HIGH', 'CRITICAL']
    
    def can_be_viewed_by(self, user):
        """Verifica se o usuário pode ver esta denúncia"""
        # Superadmins podem ver tudo
        if user.hierarchy == 'SUPERADMIN':
            return True
        
        # O denunciante pode ver sua própria denúncia
        if user == self.reporter:
            return True
        
        # Usuário designado para resolver pode ver
        if user == self.assigned_to:
            return True
            
        return False
    
    def can_be_managed_by(self, user):
        """Verifica se o usuário pode gerenciar esta denúncia"""
        return user.hierarchy in ['SUPERADMIN', 'ADMINISTRATIVO']


class ReportComment(models.Model):
    """Comentários em denúncias"""
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    comment = models.TextField(verbose_name="Comentário")
    created_at = models.DateTimeField(auto_now_add=True)
    is_internal = models.BooleanField(default=False, verbose_name="Comentário Interno")  # Só para admins
    
    class Meta:
        verbose_name = "Comentário da Denúncia"
        verbose_name_plural = "Comentários das Denúncias"
        ordering = ['created_at']
    
    def __str__(self):
        return f"Comentário de {self.user.full_name} em {self.report}"
