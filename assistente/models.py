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
    ('claude-opus-5-5', 'Claude Opus 5.5 — mais inteligente (recomendado para análises)'),
    ('claude-sonnet-5-5', 'Claude Sonnet 5.5 — equilíbrio entre velocidade e capacidade'),
    ('claude-haiku-5-5', 'Claude Haiku 5.5 — rápido e econômico'),
    # Os anteriores continuam valendo para quem já tinha escolhido.
    ('claude-sonnet-5', 'Claude Sonnet 5 (anterior)'),
    ('claude-opus-5', 'Claude Opus 5 (anterior)'),
    ('claude-haiku-4-5-20251001', 'Claude Haiku 4.5 (anterior)'),
]
MODELO_PADRAO = 'claude-opus-5-5'
MODELOS_VALIDOS = {m[0] for m in MODELOS}


class AssistenteConexao(models.Model):
    """A conexão do Claude de um usuário (uma por pessoa)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assistente_conexao')
    api_key_cifrada = models.TextField(blank=True, default='', verbose_name='Chave (cifrada)')
    key_hint = models.CharField(max_length=12, blank=True, default='', verbose_name='Final da chave')
    modelo = models.CharField(max_length=40, choices=MODELOS, default=MODELO_PADRAO)
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


class AssistenteDocumento(models.Model):
    """Um documento que o assistente criou para a pessoa (relatório, análise, ata, plano...).

    O conteúdo fica em Markdown (o mesmo que a tela já formata) e as tabelas, em
    estrutura; o Word, o Excel e o Markdown são gerados na hora de baixar, e a
    página do documento imprime como PDF.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assistente_documentos')
    titulo = models.CharField(max_length=200, verbose_name='Título')
    conteudo = models.TextField(blank=True, verbose_name='Conteúdo (Markdown)')
    tabelas = models.JSONField(default=list, blank=True, verbose_name='Tabelas')
    versao = models.PositiveIntegerField(default=1, verbose_name='Versão')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Documento do Assistente'
        verbose_name_plural = 'Documentos do Assistente'
        ordering = ['-atualizado_em']

    def __str__(self):
        return f'{self.titulo} ({self.user})'
