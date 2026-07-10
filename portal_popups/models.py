from django.conf import settings
from django.db import models
from django.utils import timezone

from .checkers import run_checker


# Cores disponíveis para o cabeçalho do popup (mapeadas para gradientes Tailwind
# no template). Chave curta armazenada; classes resolvidas na renderização.
COLOR_CHOICES = [
    ('indigo', 'Índigo'),
    ('orange', 'Laranja'),
    ('amber', 'Âmbar'),
    ('red', 'Vermelho'),
    ('green', 'Verde'),
    ('purple', 'Roxo'),
    ('blue', 'Azul'),
    ('slate', 'Cinza'),
]

COLOR_GRADIENTS = {
    'indigo': 'from-indigo-500 to-purple-600',
    'orange': 'from-orange-500 to-amber-500',
    'amber': 'from-amber-500 to-yellow-500',
    'red': 'from-red-500 to-rose-600',
    'green': 'from-green-500 to-emerald-600',
    'purple': 'from-purple-500 to-fuchsia-600',
    'blue': 'from-blue-500 to-cyan-600',
    'slate': 'from-slate-600 to-slate-800',
}


class PortalPopup(models.Model):
    """Popup configurável exibido no portal.

    Substitui a necessidade de codificar cada popup à mão: sequência, público
    (usuário/setor/hierarquia), modo de conclusão e bloqueio são definidos pela
    tela de gestão em /popups/.
    """

    # --- Conteúdo / visual ---
    title = models.CharField(max_length=150, verbose_name='Título')
    message = models.TextField(
        verbose_name='Mensagem',
        help_text='Texto exibido no corpo do popup. Quebras de linha são preservadas.',
    )
    icon = models.CharField(
        max_length=60, default='fas fa-bullhorn', blank=True,
        verbose_name='Ícone (classe Font Awesome)',
    )
    color = models.CharField(max_length=20, choices=COLOR_CHOICES, default='indigo',
                             verbose_name='Cor do cabeçalho')

    # --- Conclusão ---
    MODE_ACK = 'ACK'
    MODE_LINK = 'LINK'
    MODE_EXTERNAL = 'EXTERNAL'
    COMPLETION_MODES = [
        (MODE_ACK, 'Ciente — botão de confirmação'),
        (MODE_LINK, 'Visitar link — conclui ao abrir a ação'),
        (MODE_EXTERNAL, 'Tarefa do sistema — conclui quando a tarefa é cumprida'),
    ]
    completion_mode = models.CharField(
        max_length=20, choices=COMPLETION_MODES, default=MODE_ACK,
        verbose_name='Modo de conclusão',
    )
    action_url = models.CharField(
        max_length=300, blank=True, verbose_name='URL de ação',
        help_text='Destino do botão de ação (obrigatório para "Visitar link" e '
                  '"Tarefa do sistema"). Ex.: /feedback/pesquisa-clima/',
    )
    action_label = models.CharField(max_length=60, default='Continuar', blank=True,
                                    verbose_name='Texto do botão de ação')
    external_check_key = models.CharField(
        max_length=80, blank=True, verbose_name='Verificação da tarefa',
        help_text='Para "Tarefa do sistema": qual condição marca o popup como concluído.',
    )

    # --- Público-alvo (condições) ---
    target_all = models.BooleanField(
        default=False, verbose_name='Todos os usuários',
        help_text='Quando marcado, ignora os filtros de usuário/setor/hierarquia.',
    )
    target_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='portal_popups',
        verbose_name='Usuários específicos',
    )
    target_sectors = models.ManyToManyField(
        'users.Sector', blank=True, related_name='portal_popups',
        verbose_name='Setores',
    )
    target_hierarchies = models.JSONField(
        default=list, blank=True, verbose_name='Hierarquias',
        help_text='Lista de códigos de hierarquia (ex.: ["PADRAO", "SUPERVISOR"]).',
    )

    # --- Bloqueio ---
    BLOCK_NEVER = 'NEVER'
    BLOCK_AFTER = 'AFTER'
    BLOCK_ALWAYS = 'ALWAYS'
    BLOCKING_MODES = [
        (BLOCK_NEVER, 'Nunca bloqueia (sempre pode pular)'),
        (BLOCK_AFTER, 'Bloqueia após uma data/hora'),
        (BLOCK_ALWAYS, 'Sempre bloqueia até concluir'),
    ]
    blocking_mode = models.CharField(
        max_length=20, choices=BLOCKING_MODES, default=BLOCK_NEVER,
        verbose_name='Bloqueio do portal',
    )
    block_after = models.DateTimeField(
        null=True, blank=True, verbose_name='Bloquear a partir de',
        help_text='Só para "Bloqueia após uma data/hora": até lá o popup pode ser '
                  'pulado; depois passa a travar o portal de quem não concluiu.',
    )

    # --- Janela de exibição / estado ---
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    start_at = models.DateTimeField(null=True, blank=True, verbose_name='Exibir a partir de')
    end_at = models.DateTimeField(null=True, blank=True, verbose_name='Exibir até')
    order = models.PositiveIntegerField(default=0, verbose_name='Sequência',
                                        help_text='Menor aparece primeiro.')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='portal_popups_created', verbose_name='Criado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Popup do Portal'
        verbose_name_plural = 'Popups do Portal'
        ordering = ['order', 'id']

    def __str__(self):
        return self.title

    # --- Apresentação ---
    @property
    def gradient_classes(self):
        return COLOR_GRADIENTS.get(self.color, COLOR_GRADIENTS['indigo'])

    # --- Janela / estado ---
    def is_within_window(self, now=None):
        now = now or timezone.now()
        if self.start_at and now < self.start_at:
            return False
        if self.end_at and now > self.end_at:
            return False
        return True

    def is_blocking_now(self, now=None):
        if self.blocking_mode == self.BLOCK_ALWAYS:
            return True
        if self.blocking_mode == self.BLOCK_AFTER:
            now = now or timezone.now()
            return bool(self.block_after and now > self.block_after)
        return False

    # --- Público ---
    def applies_to(self, user):
        if not user or not user.is_authenticated:
            return False
        if self.target_all:
            return True
        if self.target_users.filter(pk=user.pk).exists():
            return True
        if self.target_hierarchies and getattr(user, 'hierarchy', None) in self.target_hierarchies:
            return True
        # Setor principal ou qualquer setor do usuário.
        target_sector_ids = set(self.target_sectors.values_list('id', flat=True))
        if target_sector_ids:
            user_sector_ids = set()
            if getattr(user, 'sector_id', None):
                user_sector_ids.add(user.sector_id)
            user_sector_ids.update(user.sectors.values_list('id', flat=True))
            if target_sector_ids & user_sector_ids:
                return True
        return False

    # --- Conclusão ---
    def is_completed_by(self, user):
        if self.completion_mode == self.MODE_EXTERNAL:
            if not self.external_check_key:
                return False
            return run_checker(self.external_check_key, user)
        return PopupCompletion.objects.filter(popup=self, user=user).exists()

    def mark_completed(self, user):
        """Registra conclusão para os modos ACK/LINK (idempotente)."""
        if self.completion_mode == self.MODE_EXTERNAL:
            return
        PopupCompletion.objects.get_or_create(popup=self, user=user)


class PopupCompletion(models.Model):
    """Conclusão de um popup por um usuário (modos 'Ciente' e 'Visitar link')."""

    popup = models.ForeignKey(PortalPopup, on_delete=models.CASCADE, related_name='completions')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='popup_completions')
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Conclusão de Popup'
        verbose_name_plural = 'Conclusões de Popup'
        unique_together = ('popup', 'user')

    def __str__(self):
        return f'{self.user} concluiu {self.popup}'
