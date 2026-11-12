"""Rotina ADM (30/10/2026): a central do ADM de loja.

Nasce só para o SUPERADMIN. Ele libera, em /rotina-adm/acessos/, grupos de
comunicação ou pessoas como **Visualizador** (vê a loja dele — setor principal —
e marca o checklist do dia) ou **Gestor** (vê qualquer loja e monta/copia o
checklist de cada uma).
"""
from django.conf import settings
from django.db import models


class AcessoRotinaAdm(models.Model):
    VISUALIZADOR = 'VISUALIZADOR'
    GESTOR = 'GESTOR'
    PAPEIS = [(VISUALIZADOR, 'Visualizador'), (GESTOR, 'Gestor')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='acesso_rotina_adm', verbose_name='Pessoa')
    grupo = models.OneToOneField('communications.CommunicationGroup', on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='acesso_rotina_adm', verbose_name='Grupo')
    papel = models.CharField(max_length=12, choices=PAPEIS, default=VISUALIZADOR, verbose_name='Papel')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Acesso à Rotina ADM'
        verbose_name_plural = 'Acessos à Rotina ADM'
        constraints = [models.CheckConstraint(
            condition=(models.Q(user__isnull=False, grupo__isnull=True) | models.Q(user__isnull=True, grupo__isnull=False)),
            name='rotina_adm_acesso_pessoa_ou_grupo')]

    def __str__(self):
        alvo = self.user or self.grupo
        return f'{alvo} — {self.get_papel_display()}'


class ItemChecklist(models.Model):
    """Um item do check-list diário de uma loja. Removido vira inativo (o histórico fica)."""

    loja = models.ForeignKey('users.Sector', on_delete=models.CASCADE, related_name='itens_checklist_adm',
                             verbose_name='Loja')
    titulo = models.CharField(max_length=200, verbose_name='Item')
    horario = models.TimeField(null=True, blank=True, verbose_name='Até que horas')
    ordem = models.PositiveIntegerField(default=0)
    ativo = models.BooleanField(default=True, db_index=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Item do check-list da loja'
        verbose_name_plural = 'Itens do check-list da loja'
        ordering = ['loja', 'ordem', 'horario', 'id']

    def __str__(self):
        return f'{self.loja} — {self.titulo}'


class MarcacaoChecklist(models.Model):
    """O item foi feito naquele dia (um por item e dia)."""

    item = models.ForeignKey(ItemChecklist, on_delete=models.CASCADE, related_name='marcacoes')
    data = models.DateField(db_index=True)
    feito_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='+')
    feito_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Item do check-list feito'
        verbose_name_plural = 'Itens do check-list feitos'
        constraints = [models.UniqueConstraint(fields=['item', 'data'], name='rotina_adm_marcacao_unica')]


class ConfiguracaoRotinaAdm(models.Model):
    """Uma linha só. O SUPERADMIN cola o link do grupo dos ADMs de loja no WhatsApp (09/11/2026)."""

    link_grupo_whatsapp = models.URLField(max_length=300, blank=True, default='', db_default='',
                                          verbose_name='Link do grupo dos ADMs no WhatsApp')
    # Broche de analista destaque (12/11/2026): quem chega nos dois mínimos do mês no
    # ranking (/rotina-adm/ranking/) fica elegível. O SUPERADMIN ajusta na própria tela.
    broche_pct_minimo = models.PositiveSmallIntegerField(
        default=90, db_default=90, verbose_name='Broche: mínimo de itens concluídos (%)')
    broche_no_prazo_minimo = models.PositiveSmallIntegerField(
        default=80, db_default=80, verbose_name='Broche: mínimo de itens feitos no prazo (%)')
    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       blank=True, related_name='+')

    class Meta:
        verbose_name = 'Configuração da Rotina ADM'
        verbose_name_plural = 'Configuração da Rotina ADM'

    @classmethod
    def get(cls):
        cfg, _ = cls.objects.get_or_create(pk=1)
        return cfg
