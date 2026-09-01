"""Cursos obrigatórios da Vivo — o portal só guarda o comprovante.

O curso acontece na plataforma da Vivo. Aqui a gente publica o link e as
orientações, recebe o comprovante e mostra num quadro quem fez e quem não fez.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


def get_media_storage():
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


def caminho_comprovante(instance, filename):
    hoje = timezone.localdate()
    return f'cursos/comprovantes/{hoje:%Y/%m}/{instance.colaborador_id}_{filename}'


class ConfiguracaoCursos(models.Model):
    """Uma linha só. Quem manda no módulo e quem é cobrado por ele."""

    bloquear_navegacao = models.BooleanField(
        default=False, verbose_name='Travar o portal de quem está com curso vencido',
        help_text='Nasce desligado de propósito: confira o quadro com dados reais antes de ligar.')

    grupos = models.ManyToManyField(
        'communications.CommunicationGroup', blank=True,
        related_name='cursos_config', verbose_name='Grupos cobrados')
    setores = models.ManyToManyField(
        'users.Sector', blank=True,
        related_name='cursos_config', verbose_name='Setores cobrados')
    usuarios = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name='cursos_cobrado_em', verbose_name='Pessoas cobradas',
        help_text='Escolhidas uma a uma. Somam-se aos grupos e setores — servem '
                  'para incluir quem não está em nenhum deles.')
    gestores = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name='cursos_gestor_de', verbose_name='Gestores do módulo',
        help_text='Publicam o curso do mês, escrevem as orientações e conferem os comprovantes.')

    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração de Cursos'
        verbose_name_plural = 'Configuração de Cursos'

    def __str__(self):
        return 'Configuração de Cursos'

    @classmethod
    def get(cls):
        obj = cls.objects.prefetch_related(
            'grupos', 'setores', 'gestores', 'usuarios').first()
        return obj or cls.objects.create()

    def no_escopo(self, user):
        """A pessoa é cobrada pelos cursos?

        Três caminhos que se somam: estar num grupo cobrado, num setor cobrado
        ou ter sido escolhido na mão. O terceiro existe para o caso que os dois
        primeiros não resolvem — alguém que precisa fazer o curso mas não está
        em nenhum grupo ou setor da lista.

        Sem nada marcado, ninguém é cobrado: o módulo entra em operação só
        depois que alguém disser quem entra nele.
        """
        if not (user and user.is_authenticated and user.is_active):
            return False

        if self.usuarios.filter(id=user.id).exists():
            return True

        ids_grupos = {g.id for g in self.grupos.all()}
        if ids_grupos and set(user.communication_groups.values_list('id', flat=True)) & ids_grupos:
            return True

        ids_setores = {s.id for s in self.setores.all()}
        if not ids_setores:
            return False
        meus = set(user.sectors.values_list('id', flat=True))
        if user.sector_id:
            meus.add(user.sector_id)
        return bool(meus & ids_setores)

    def e_gestor(self, user):
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN':
            return True
        return self.gestores.filter(id=user.id).exists()


class Curso(models.Model):
    """Um curso publicado por um gestor."""

    FOCO = 'FOCO'
    CAPACITACAO = 'CAPACITACAO'
    TIPOS = [
        (FOCO, 'Curso Foco (mensal)'),
        (CAPACITACAO, 'Capacitação Inicial (sob demanda)'),
    ]

    tipo = models.CharField(max_length=12, choices=TIPOS, default=FOCO, verbose_name='Tipo')
    titulo = models.CharField(max_length=200, verbose_name='Título')
    orientacoes = models.TextField(
        blank=True, verbose_name='Orientações',
        help_text='O que a pessoa precisa saber para fazer o curso. Aparece na tela dela.')
    link = models.URLField(max_length=500, blank=True, verbose_name='Link do curso')
    competencia = models.DateField(
        null=True, blank=True, verbose_name='Mês de referência',
        help_text='Só para o Curso Foco. Guarda sempre o dia 1º do mês.')
    prazo = models.DateField(verbose_name='Prazo para anexar o comprovante')
    publicado = models.BooleanField(default=False, verbose_name='Publicado')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cursos_criados', verbose_name='Criado por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Curso'
        verbose_name_plural = 'Cursos'
        ordering = ['-prazo', '-id']

    def __str__(self):
        return self.titulo

    @property
    def vencido(self):
        return self.prazo < timezone.localdate()

    @property
    def dias_restantes(self):
        return (self.prazo - timezone.localdate()).days

    def alcanca(self, user, config=None):
        """Este curso é cobrado desta pessoa?"""
        if not self.publicado:
            return False
        if self.tipo == self.CAPACITACAO:
            return self.atribuicoes.filter(colaborador=user).exists()
        config = config or ConfiguracaoCursos.get()
        return config.no_escopo(user)


class AtribuicaoCurso(models.Model):
    """Capacitação inicial: só faz quem o gestor sinalizar."""

    curso = models.ForeignKey(Curso, on_delete=models.CASCADE, related_name='atribuicoes')
    colaborador = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cursos_atribuidos')
    atribuido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cursos_atribuidos_por_mim')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Atribuição de curso'
        verbose_name_plural = 'Atribuições de curso'
        unique_together = [('curso', 'colaborador')]

    def __str__(self):
        return f'{self.colaborador} · {self.curso}'


class Comprovante(models.Model):
    """O anexo que a pessoa manda provando que fez o curso."""

    PENDENTE = 'PENDENTE'
    APROVADO = 'APROVADO'
    RECUSADO = 'RECUSADO'
    STATUS = [
        (PENDENTE, 'Aguardando conferência'),
        (APROVADO, 'Aprovado'),
        (RECUSADO, 'Recusado'),
    ]

    curso = models.ForeignKey(Curso, on_delete=models.CASCADE, related_name='comprovantes')
    colaborador = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='comprovantes_curso')
    arquivo = models.FileField(
        upload_to=caminho_comprovante, storage=get_media_storage(), verbose_name='Comprovante')
    nome_original = models.CharField(max_length=255, blank=True)
    tamanho = models.PositiveIntegerField(default=0)
    enviado_em = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=10, choices=STATUS, default=PENDENTE)
    observacao = models.TextField(blank=True, verbose_name='Observação do gestor')
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='comprovantes_revisados')
    revisado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Comprovante de curso'
        verbose_name_plural = 'Comprovantes de curso'
        ordering = ['-enviado_em']
        indexes = [models.Index(fields=['curso', 'colaborador'])]

    def __str__(self):
        return f'{self.colaborador} · {self.curso} · {self.status}'

    @property
    def vale_como_entregue(self):
        """Recusado volta a contar como pendência; o resto vale."""
        return self.status in (self.PENDENTE, self.APROVADO)
