"""Feed da rede (25/11/2026): um feedzinho social dentro do portal.

Pedido: "a pessoa deve poder tirar fotos ou vídeos e postar como se fosse uma rede social mesmo, sem a
burocracia de comunicados — só a foto/vídeo, descrição opcional, comentários, e aparecem os 3 últimos
comentários".

Qualquer pessoa logada vê e posta. Quem postou e o SUPERADMIN apagam o post; o comentário sai pelo autor
dele, pelo dono do post ou pelo SUPERADMIN. Apagar é desativar (``ativo=False``): o arquivo fica no
MinIO para o histórico. Os totais de curtidas e comentários ficam no próprio post (o feed não conta
linha por linha a cada carregamento).
"""
import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def caminho_da_midia(instancia, nome):
    extensao = os.path.splitext(nome)[1].lower()[:6] or '.bin'
    agora = timezone.now()
    return f'social/{agora:%Y/%m}/{uuid.uuid4().hex}{extensao}'


class Post(models.Model):
    IMAGEM = 'IMAGEM'
    VIDEO = 'VIDEO'
    TIPOS = [(IMAGEM, 'Foto'), (VIDEO, 'Vídeo')]

    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='posts_sociais')
    midia = models.FileField(upload_to=caminho_da_midia, max_length=300, verbose_name='Foto ou vídeo')
    tipo = models.CharField(max_length=6, choices=TIPOS)
    # Tamanho da imagem/vídeo: a tela reserva o espaço certo e nada pula enquanto carrega.
    largura = models.PositiveIntegerField(null=True, blank=True)
    altura = models.PositiveIntegerField(null=True, blank=True)
    legenda = models.TextField(blank=True, default='', max_length=2200, verbose_name='Descrição')
    total_curtidas = models.PositiveIntegerField(default=0)
    total_comentarios = models.PositiveIntegerField(default=0)
    ativo = models.BooleanField(default=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)
    apagado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')

    class Meta:
        verbose_name = 'Post do feed'
        verbose_name_plural = 'Posts do feed'
        ordering = ['-criado_em', '-id']

    def __str__(self):
        return f'{self.autor} — {self.get_tipo_display()} de {self.criado_em:%d/%m/%Y %H:%M}'

    @property
    def proporcao(self):
        """Altura/largura em % para reservar o espaço (limitada entre 4:5 em pé e 1,91:1 deitado)."""
        if not (self.largura and self.altura):
            return 100 if self.tipo == self.IMAGEM else 56.25
        return round(max(52.36, min(125.0, self.altura * 100 / self.largura)), 2)


class Curtida(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='curtidas')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='curtidas_sociais')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['post', 'user'], name='social_curtida_unica')]


class Comentario(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comentarios')
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='comentarios_sociais')
    texto = models.TextField(max_length=1000)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['criado_em', 'id']
        indexes = [models.Index(fields=['post', 'ativo', 'criado_em'])]
