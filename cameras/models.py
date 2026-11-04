"""Câmeras das lojas: gravadores, canais e alertas de possível furto.

O vídeo não passa pelo banco: o agente instalado na loja (pasta agente_cameras) manda fotos curtas,
que ficam só no cache, e os alertas. Alerta é suspeita para uma pessoa conferir, nunca acusação.
"""
import hashlib
import secrets
import uuid
from datetime import time

from django.conf import settings
from django.db import models
from django.utils import timezone

from webmail.seguranca import cifrar, decifrar

MINUTOS_ONLINE = 3


class ConfigCameras(models.Model):
    """Uma linha só. Nasce desligado: a loja só começa a mandar imagem depois do aviso à rede."""
    ativo = models.BooleanField(default=False, verbose_name='Receber imagens e alertas das lojas')
    alterado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    alterado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração das câmeras'

    @classmethod
    def atual(cls):
        return cls.objects.get_or_create(pk=1)[0]


class Gravador(models.Model):
    loja = models.CharField(max_length=120, verbose_name='Loja')
    setor = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Setor no portal')
    coordenador = models.CharField(max_length=120, blank=True, verbose_name='Coordenação')
    aplicativo = models.CharField(max_length=80, blank=True, verbose_name='Aplicativo')
    serial = models.CharField(max_length=60, blank=True, verbose_name='Serial')
    usuario = models.CharField(max_length=80, blank=True, verbose_name='Usuário')
    senha_cifrada = models.TextField(blank=True, editable=False)
    # Sem gravador na loja (planilha com 0): fica na lista, para não parecer esquecida.
    tem_equipamento = models.BooleanField(default=True, verbose_name='Loja tem gravador')

    # Como o agente acha o gravador dentro da rede da loja.
    ip_local = models.CharField(max_length=64, blank=True, verbose_name='IP do gravador na loja')
    porta_rtsp = models.PositiveIntegerField(default=554, verbose_name='Porta RTSP')
    caminho_rtsp = models.CharField(max_length=160, default='/cam/realmonitor?channel={canal}&subtype=1', verbose_name='Caminho RTSP')
    abre_as = models.TimeField(default=time(8, 0), verbose_name='Abre às')
    fecha_as = models.TimeField(default=time(20, 0), verbose_name='Fecha às')

    token_hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)
    ultimo_contato = models.DateTimeField(null=True, blank=True, editable=False)
    versao_agente = models.CharField(max_length=30, blank=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Gravador'
        verbose_name_plural = 'Gravadores'
        ordering = ['loja']

    def __str__(self):
        return self.loja

    def definir_senha(self, senha):
        self.senha_cifrada = cifrar(senha) if senha else ''

    @property
    def senha(self):
        return decifrar(self.senha_cifrada) if self.senha_cifrada else ''

    def gerar_token(self):
        """Devolve o token uma única vez; no banco fica só o resumo."""
        token = secrets.token_urlsafe(32)
        self.token_hash = self.resumo(token)
        self.save(update_fields=['token_hash'])
        return token

    @staticmethod
    def resumo(token):
        return hashlib.sha256((token or '').encode()).hexdigest()

    @property
    def online(self):
        return bool(self.ultimo_contato and (timezone.now() - self.ultimo_contato).total_seconds() < MINUTOS_ONLINE * 60)

    @property
    def situacao(self):
        if not self.tem_equipamento:
            return 'sem_equipamento'
        if not self.token_hash:
            return 'sem_agente'
        return 'online' if self.online else 'offline'


class Canal(models.Model):
    gravador = models.ForeignKey(Gravador, on_delete=models.CASCADE, related_name='canais')
    numero = models.PositiveIntegerField(verbose_name='Canal')
    nome = models.CharField(max_length=80, blank=True, verbose_name='Nome')
    ativo = models.BooleanField(default=True, verbose_name='Mostrar')
    ia_ativa = models.BooleanField(default=True, verbose_name='Analisar')
    # [{"nome": "Mostruário", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.2}], em fração da imagem.
    zonas = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['gravador__loja', 'numero']
        unique_together = ('gravador', 'numero')

    def __str__(self):
        return f'{self.gravador.loja} · {self.titulo}'

    @property
    def titulo(self):
        return self.nome or f'Câmera {self.numero}'


def _caminho_foto(instancia, nome):
    # Nome impossível de adivinhar: a foto mostra pessoas, e é servida só pela view do módulo.
    agora = timezone.now()
    return f'cameras/alertas/{agora:%Y/%m}/{uuid.uuid4().hex}.jpg'


class AlertaCamera(models.Model):
    class Tipo(models.TextChoices):
        ITEM_SUMIU = 'ITEM_SUMIU', 'Aparelho saiu do mostruário'
        FORA_HORARIO = 'FORA_HORARIO', 'Pessoa com a loja fechada'
        PERMANENCIA = 'PERMANENCIA', 'Permanência longa na área'

    class Status(models.TextChoices):
        NOVO = 'NOVO', 'Novo'
        ANALISE = 'ANALISE', 'Em análise'
        CONFIRMADO = 'CONFIRMADO', 'Furto confirmado'
        FALSO = 'FALSO', 'Falso alarme'

    gravador = models.ForeignKey(Gravador, on_delete=models.CASCADE, related_name='alertas')
    canal = models.ForeignKey(Canal, on_delete=models.SET_NULL, null=True, blank=True, related_name='alertas')
    tipo = models.CharField(max_length=14, choices=Tipo.choices)
    confianca = models.PositiveSmallIntegerField(default=0, verbose_name='Confiança (%)')
    motivo = models.CharField(max_length=255, blank=True)
    foto = models.FileField(upload_to=_caminho_foto, blank=True)
    repeticoes = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NOVO, db_index=True)
    observacao = models.TextField(blank=True)
    tratado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    tratado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)
    ultimo_em = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Alerta de câmera'
        verbose_name_plural = 'Alertas de câmera'
        ordering = ['-criado_em']


class AcessoCamera(models.Model):
    """Quem olhou o quê. Imagem de pessoa é dado pessoal (LGPD): precisa dar para prestar contas."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    acao = models.CharField(max_length=40)
    gravador = models.ForeignKey(Gravador, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    detalhe = models.CharField(max_length=160, blank=True)
    em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Acesso às câmeras'
        verbose_name_plural = 'Acessos às câmeras'
        ordering = ['-em']
