"""Facilitador: arquivos e links úteis do dia a dia, num lugar só.

Todo mundo vê a grade de cards; só o SUPERADMIN cadastra, edita, oculta e exclui.
Card de arquivo abre a descrição e o botão de baixar; card de link abre a
explicação, o botão para ir ao link e o QR code dele.
"""
import os

from django.conf import settings
from django.core.validators import URLValidator
from django.db import models
from django.utils import timezone


def get_media_storage():
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


def caminho_arquivo(instance, filename):
    hoje = timezone.localdate()
    return f'facilitador/{hoje:%Y/%m}/{filename}'


# Extensão → (ícone Font Awesome, cor do card). O resto cai em "arquivo".
ICONES = {
    'pdf': ('fa-file-pdf', 'red'),
    'doc': ('fa-file-word', 'blue'), 'docx': ('fa-file-word', 'blue'), 'odt': ('fa-file-word', 'blue'),
    'xls': ('fa-file-excel', 'emerald'), 'xlsx': ('fa-file-excel', 'emerald'), 'csv': ('fa-file-csv', 'emerald'),
    'ods': ('fa-file-excel', 'emerald'),
    'ppt': ('fa-file-powerpoint', 'orange'), 'pptx': ('fa-file-powerpoint', 'orange'),
    'png': ('fa-file-image', 'pink'), 'jpg': ('fa-file-image', 'pink'), 'jpeg': ('fa-file-image', 'pink'),
    'gif': ('fa-file-image', 'pink'), 'webp': ('fa-file-image', 'pink'), 'svg': ('fa-file-image', 'pink'),
    'mp4': ('fa-file-video', 'violet'), 'mov': ('fa-file-video', 'violet'), 'webm': ('fa-file-video', 'violet'),
    'mp3': ('fa-file-audio', 'cyan'), 'wav': ('fa-file-audio', 'cyan'), 'ogg': ('fa-file-audio', 'cyan'),
    'zip': ('fa-file-zipper', 'amber'), 'rar': ('fa-file-zipper', 'amber'), '7z': ('fa-file-zipper', 'amber'),
    'apk': ('fa-mobile-screen', 'lime'), 'txt': ('fa-file-lines', 'gray'),
}


class Recurso(models.Model):
    TIPO_ARQUIVO = 'ARQUIVO'
    TIPO_LINK = 'LINK'
    TIPOS = [(TIPO_ARQUIVO, 'Arquivo'), (TIPO_LINK, 'Link')]

    titulo = models.CharField('Título', max_length=150)
    descricao = models.TextField('Descrição / explicação', blank=True)
    tipo = models.CharField('Tipo', max_length=10, choices=TIPOS, default=TIPO_ARQUIVO)
    categoria = models.CharField('Categoria', max_length=60, blank=True,
                                 help_text='Agrupa os cards e vira filtro (ex.: RH, Vendas, Sistemas).')

    arquivo = models.FileField('Arquivo', upload_to=caminho_arquivo, storage=get_media_storage(),
                               max_length=500, blank=True)
    nome_original = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    tamanho = models.PositiveBigIntegerField(default=0)

    url = models.URLField('Link', max_length=1000, blank=True,
                          validators=[URLValidator(schemes=['http', 'https'])])

    ordem = models.PositiveIntegerField('Ordem', default=0, help_text='Menor aparece primeiro.')
    ativo = models.BooleanField('Visível para todos', default=True)
    acessos = models.PositiveIntegerField(default=0, help_text='Downloads ou cliques no link.')

    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='facilitador_recursos')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['ordem', 'titulo']
        verbose_name = 'Recurso do Facilitador'
        verbose_name_plural = 'Recursos do Facilitador'

    def __str__(self):
        return self.titulo

    @property
    def e_link(self):
        return self.tipo == self.TIPO_LINK

    @property
    def extensao(self):
        nome = self.nome_original or (self.arquivo.name if self.arquivo else '')
        return os.path.splitext(nome)[1].lower().lstrip('.')

    @property
    def icone(self):
        if self.e_link:
            return 'fa-link'
        return ICONES.get(self.extensao, ('fa-file', 'slate'))[0]

    @property
    def cor(self):
        if self.e_link:
            return 'indigo'
        return ICONES.get(self.extensao, ('fa-file', 'slate'))[1]

    @property
    def e_imagem(self):
        return self.extensao in ('png', 'jpg', 'jpeg', 'gif', 'webp')

    @property
    def e_pdf(self):
        return self.extensao == 'pdf'

    @property
    def dominio(self):
        from urllib.parse import urlparse
        return urlparse(self.url).netloc.removeprefix('www.') if self.url else ''
