"""Sessões do app mobile (Android/iOS).

O portal web usa sessão com cookie; o app usa um token por aparelho. Guardamos
só o hash (sha256) do token: quem lê o banco não consegue entrar como ninguém.
Cada aparelho tem o seu — sair num celular não derruba o outro, e o SUPERADMIN
pode revogar um aparelho sem trocar a senha da pessoa.
"""
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def hash_do_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class TokenMobile(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='tokens_mobile', verbose_name='Usuário')
    chave_hash = models.CharField('Hash do token', max_length=64, unique=True)
    prefixo = models.CharField('Início do token', max_length=8, help_text='Só para reconhecer o aparelho na lista.')
    dispositivo = models.CharField('Aparelho', max_length=120, blank=True)
    plataforma = models.CharField('Plataforma', max_length=20, blank=True)
    versao_app = models.CharField('Versão do app', max_length=20, blank=True)
    criado_em = models.DateTimeField('Criado em', auto_now_add=True)
    ultimo_uso = models.DateTimeField('Último uso', null=True, blank=True)
    revogado_em = models.DateTimeField('Revogado em', null=True, blank=True)

    class Meta:
        verbose_name = 'Sessão do app'
        verbose_name_plural = 'Sessões do app'
        ordering = ['-criado_em']
        indexes = [models.Index(fields=['user', 'revogado_em'])]

    def __str__(self):
        return f'{self.user} — {self.dispositivo or self.plataforma or "app"} ({self.prefixo}…)'

    @classmethod
    def emitir(cls, user, *, dispositivo='', plataforma='', versao_app=''):
        """Cria a sessão e devolve (registro, token em texto). O texto não fica salvo."""
        token = secrets.token_urlsafe(32)
        registro = cls.objects.create(
            user=user, chave_hash=hash_do_token(token), prefixo=token[:8],
            dispositivo=(dispositivo or '')[:120], plataforma=(plataforma or '')[:20],
            versao_app=(versao_app or '')[:20], ultimo_uso=timezone.now())
        return registro, token

    def revogar(self):
        if self.revogado_em is None:
            self.revogado_em = timezone.now()
            self.save(update_fields=['revogado_em'])
