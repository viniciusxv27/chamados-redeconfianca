"""Modelos do Assistente Claude.

A conexão guarda a chave da Anthropic do usuário (cifrada) e a preferência de
modelo. As mensagens guardam o histórico visível do chat (texto), para a
conversa continuar entre visitas — as chamadas de ferramenta acontecem dentro
de cada pergunta e não são persistidas.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

MODELOS = [
    ('claude-sonnet-5', 'Claude Sonnet 5 — equilíbrio (recomendado)'),
    ('claude-opus-5', 'Claude Opus 5 — mais capaz'),
    ('claude-haiku-4-5-20251001', 'Claude Haiku 4.5 — rápido e econômico'),
]
MODELOS_VALIDOS = {m[0] for m in MODELOS}


class AssistenteConexao(models.Model):
    """A conexão do Claude de um usuário (uma por pessoa)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assistente_conexao')
    api_key_cifrada = models.TextField(blank=True, default='', verbose_name='Chave (cifrada)')
    key_hint = models.CharField(max_length=12, blank=True, default='', verbose_name='Final da chave')
    modelo = models.CharField(max_length=40, choices=MODELOS, default='claude-sonnet-5')
    conectado_em = models.DateTimeField(null=True, blank=True)
    ultimo_ok_em = models.DateTimeField(null=True, blank=True, verbose_name='Último teste OK')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Conexão do Assistente'
        verbose_name_plural = 'Conexões do Assistente'

    def __str__(self):
        return f'Assistente de {self.user}'

    @property
    def conectado(self):
        return bool(self.api_key_cifrada)

    def set_api_key(self, raw):
        from .crypto import cifrar
        raw = (raw or '').strip()
        if raw:
            self.api_key_cifrada = cifrar(raw)
            self.key_hint = '…' + raw[-4:] if len(raw) >= 4 else '…'
            self.conectado_em = timezone.now()
        else:
            self.api_key_cifrada = ''
            self.key_hint = ''
            self.conectado_em = None

    def get_api_key(self):
        from .crypto import decifrar
        if not self.api_key_cifrada:
            return ''
        try:
            return decifrar(self.api_key_cifrada)
        except Exception:
            return ''


class AssistenteMensagem(models.Model):
    """Uma fala do chat (só o texto visível; ferramentas não entram aqui)."""

    USUARIO = 'user'
    ASSISTENTE = 'assistant'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assistente_mensagens')
    papel = models.CharField(max_length=12, choices=[(USUARIO, 'Usuário'), (ASSISTENTE, 'Assistente')])
    conteudo = models.TextField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Mensagem do Assistente'
        verbose_name_plural = 'Mensagens do Assistente'
        ordering = ['criado_em']
        indexes = [models.Index(fields=['user', 'criado_em'])]

    def __str__(self):
        return f'{self.user} · {self.papel}: {self.conteudo[:40]}'
