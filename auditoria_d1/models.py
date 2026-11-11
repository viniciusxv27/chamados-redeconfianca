"""Auditoria D-1 (11/11/2026): esteira de verificação de vendas portada do sistema Flask para o portal.

Os dados da auditoria continuam no MySQL do VivoGo (vivogo.verificacao_vendas e companhia, ver mysql.py).
Aqui, no Postgres do portal, fica só o que é do portal: quem acessa, a configuração do WhatsApp e o registro
de cada mensagem disparada para as lojas.
"""
from django.conf import settings
from django.db import models


class AcessoAuditoriaD1(models.Model):
    """Pessoa liberada pelo SUPERADMIN em /auditoria-d1/acessos/ (o SUPERADMIN entra sempre)."""

    CONSULTA = 'CONSULTA'
    AUDITOR = 'AUDITOR'
    GESTOR = 'GESTOR'
    PAPEIS = [
        (CONSULTA, 'Consulta'),
        (AUDITOR, 'Auditor'),
        (GESTOR, 'Gestor'),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='acesso_auditoria_d1', verbose_name='Pessoa')
    papel = models.CharField(max_length=10, choices=PAPEIS, default=CONSULTA, db_default=CONSULTA,
                             verbose_name='Papel')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Acesso à Auditoria D-1'
        verbose_name_plural = 'Acessos à Auditoria D-1'

    def __str__(self):
        return f'{self.user} — {self.get_papel_display()}'


class ConfiguracaoAuditoriaD1(models.Model):
    """Uma linha só. O WhatsApp das lojas nasce DESLIGADO; só o SUPERADMIN liga."""

    whatsapp_ativo = models.BooleanField(default=False, db_default=False,
                                         verbose_name='Permitir envio de WhatsApp (SVA não ativado)')
    grupo_teste = models.CharField(max_length=120, blank=True, default='', db_default='',
                                   verbose_name='ID do grupo de teste no WhatsApp')
    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')

    class Meta:
        verbose_name = 'Configuração da Auditoria D-1'
        verbose_name_plural = 'Configuração da Auditoria D-1'

    @classmethod
    def get(cls):
        cfg, _ = cls.objects.get_or_create(pk=1)
        return cfg


class DisparoWhatsappD1(models.Model):
    """Uma mensagem de SVA não ativado para o grupo de uma loja (ou para o grupo de teste) — gravada antes de sair."""

    importacao_id = models.IntegerField(verbose_name='Importação SVA (MySQL)')
    anomes = models.CharField(max_length=6)
    teste = models.BooleanField(default=False, db_default=False)
    loja = models.CharField(max_length=120, verbose_name='PDV')
    setor = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    destino = models.CharField(max_length=120)
    texto = models.TextField()
    enviado = models.BooleanField(null=True, blank=True)
    detalhe = models.CharField(max_length=255, blank=True, default='', db_default='')
    disparado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'WhatsApp da Auditoria D-1'
        verbose_name_plural = 'WhatsApp da Auditoria D-1'
        ordering = ['-criado_em', '-id']
