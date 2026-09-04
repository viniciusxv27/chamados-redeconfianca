"""Banco de talentos: os currículos que o RH recebe, prontos para consulta.

O RH recebe dezenas de currículos por dia, para vagas diferentes, e hoje eles
morrem numa pasta de e-mail. Aqui cada PDF entra uma vez, o portal lê nome,
endereço e experiência, e a busca responde em linguagem de gente — "vendedor
para loja de viana" — em vez de exigir filtro por campo.

Quem foi contratado sai da busca, mas continua no banco: o histórico é o que
permite saber que aquela pessoa já passou por aqui.
"""
from django.conf import settings
from django.db import models

from core.storage import get_media_storage


def caminho_curriculo(instance, filename):
    from django.utils import timezone
    hoje = timezone.localdate()
    return f'curriculos/{hoje:%Y/%m}/{filename}'


class Curriculo(models.Model):
    """Um currículo recebido, com o que o portal conseguiu ler dele."""

    class Situacao(models.TextChoices):
        NOVO = 'NOVO', 'No banco'
        ENTREVISTA = 'ENTREVISTA', 'Em entrevista'
        CONTRATADO = 'CONTRATADO', 'Contratado'
        DESCARTADO = 'DESCARTADO', 'Descartado'

    arquivo = models.FileField(
        upload_to=caminho_curriculo, storage=get_media_storage(),
        verbose_name='Currículo (PDF)')
    nome_arquivo = models.CharField(max_length=255, blank=True)

    # ── O que a leitura do PDF extraiu ──────────────────────────────────────
    nome = models.CharField(max_length=180, blank=True, db_index=True,
                            verbose_name='Nome')
    endereco = models.CharField(max_length=300, blank=True, verbose_name='Endereço')
    cidade = models.CharField(max_length=120, blank=True, db_index=True,
                              verbose_name='Cidade')
    bairro = models.CharField(max_length=120, blank=True, verbose_name='Bairro')
    telefone = models.CharField(max_length=40, blank=True, verbose_name='Telefone')
    email = models.EmailField(blank=True, verbose_name='E-mail')

    experiencia = models.TextField(blank=True, verbose_name='Experiência')
    cargos = models.TextField(
        blank=True, verbose_name='Cargos identificados',
        help_text='Um por linha. É o que a busca por vaga compara primeiro.')

    texto = models.TextField(blank=True, verbose_name='Texto do PDF')
    # Cópia sem acento e em minúsculas de tudo o que é pesquisável. Existe para
    # a busca não depender de extensão do Postgres (unaccent/pg_trgm não estão
    # instaladas neste banco) nem de a pessoa digitar com acento.
    busca = models.TextField(blank=True, editable=False)

    # ── Situação ────────────────────────────────────────────────────────────
    situacao = models.CharField(
        max_length=12, choices=Situacao.choices, default=Situacao.NOVO,
        db_index=True, verbose_name='Situação')
    contratado_em = models.DateField(null=True, blank=True, verbose_name='Contratado em')
    contratado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='curriculos_contratados', verbose_name='Marcado por')
    observacao = models.TextField(blank=True, verbose_name='Observação do RH')

    # ── Entrevista no sistema de perfil (IA do RH) ──────────────────────────
    entrevista_token = models.CharField(
        max_length=80, blank=True, db_index=True,
        verbose_name='Token da entrevista',
        help_text='Token do link público no sistema de perfil.')
    entrevista_em = models.DateTimeField(null=True, blank=True,
                                         verbose_name='Entrevista em')
    reuniao = models.ForeignKey(
        'reunioes.Reuniao', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='curriculos', verbose_name='Reunião de entrevista')

    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='curriculos_enviados', verbose_name='Importado por')
    enviado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Currículo'
        verbose_name_plural = 'Currículos'
        ordering = ['-enviado_em']
        indexes = [
            models.Index(fields=['situacao', '-enviado_em']),
        ]

    def __str__(self):
        return self.nome or self.nome_arquivo or f'Currículo {self.pk}'

    @property
    def contratado(self):
        return self.situacao == self.Situacao.CONTRATADO

    @property
    def disponivel(self):
        """Entra nas buscas por vaga? Contratado e descartado não entram."""
        return self.situacao in (self.Situacao.NOVO, self.Situacao.ENTREVISTA)

    @property
    def lista_cargos(self):
        return [c.strip() for c in (self.cargos or '').splitlines() if c.strip()]

    @property
    def link_entrevista(self):
        """Link público da entrevista no sistema de perfil, se houver."""
        if not self.entrevista_token:
            return ''
        from .integracao import url_da_entrevista
        return url_da_entrevista(self.entrevista_token)

    def montar_busca(self):
        """Refaz o blob de busca. Chamado no save, nunca na mão."""
        from .texto import normalizar

        partes = [self.nome, self.endereco, self.cidade, self.bairro,
                  self.cargos, self.experiencia, self.texto]
        return normalizar(' \n '.join(p for p in partes if p))

    def save(self, *args, **kwargs):
        self.busca = self.montar_busca()
        super().save(*args, **kwargs)


class ConfiguracaoCurriculos(models.Model):
    """Uma linha só. Quem usa o banco de talentos e onde fica a IA do RH."""

    grupos = models.ManyToManyField(
        'communications.CommunicationGroup', blank=True, related_name='+',
        verbose_name='Grupos com acesso',
        help_text='Quem enxerga e usa o banco de talentos, além do SUPERADMIN.')

    url_sistema_perfil = models.URLField(
        max_length=300, blank=True,
        default='https://rede-confianca-sistema-perfil.lpl0df.easypanel.host',
        verbose_name='Endereço do sistema de perfil',
        help_text='Onde vive a IA do RH. Usado para montar o link da entrevista.')

    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+')

    class Meta:
        verbose_name = 'Configuração do banco de talentos'
        verbose_name_plural = 'Configuração do banco de talentos'

    def __str__(self):
        return 'Configuração do Banco de Talentos'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
