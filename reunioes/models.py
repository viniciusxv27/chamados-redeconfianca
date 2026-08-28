"""Reuniões do portal: agenda, sala de vídeo e ata.

A sala de vídeo é uma sala Jitsi embutida na página — sem limite de tempo, com
câmera, microfone, tela compartilhada, chat e mão levantada. O nome da sala é
sorteado: sala Jitsi é pública para quem sabe o nome, então nome adivinhável
seria porta aberta para a reunião da diretoria.

A ata reaproveita o pipeline de transcrição que já existe na agenda
(``agenda.MeetingTranscription``): grava, transcreve, resume e distribui.
"""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class ConfiguracaoReunioes(models.Model):
    """Uma linha só."""

    servidor_jitsi = models.CharField(
        max_length=200, default='meet.jit.si', verbose_name='Servidor de vídeo (Jitsi)',
        help_text='Servidor público por padrão. Trocar por um servidor da empresa '
                  'é mudar só este campo.')
    gerar_ata = models.BooleanField(
        default=True, verbose_name='Gerar ata automaticamente',
        help_text='Ao encerrar a gravação, a IA monta a ata e envia para quem estava na agenda.')

    # Credenciais do 8x8 (JaaS). Com elas preenchidas, o portal assina a entrada
    # de cada pessoa: ninguém cai na tela "aguardando um moderador" e o nome vai
    # no token, sem passar pelo navegador.
    jaas_app_id = models.CharField(
        max_length=120, blank=True, verbose_name='JaaS — AppID',
        help_text='Começa com "vpaas-magic-cookie-".')
    jaas_api_key_id = models.CharField(
        max_length=120, blank=True, verbose_name='JaaS — ID da chave (kid)')
    jaas_chave_privada = models.TextField(
        blank=True, verbose_name='JaaS — chave privada (PEM)',
        help_text='O arquivo .pk baixado no painel do 8x8, colado inteiro.')

    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração de Reuniões'
        verbose_name_plural = 'Configuração de Reuniões'

    def __str__(self):
        return 'Configuração de Reuniões'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


def nova_sala():
    """Nome de sala impossível de adivinhar."""
    return f'rc-{secrets.token_urlsafe(16)}'


class Reuniao(models.Model):
    AGENDADA = 'AGENDADA'
    EM_ANDAMENTO = 'EM_ANDAMENTO'
    ENCERRADA = 'ENCERRADA'
    CANCELADA = 'CANCELADA'
    STATUS = [
        (AGENDADA, 'Agendada'),
        (EM_ANDAMENTO, 'Em andamento'),
        (ENCERRADA, 'Encerrada'),
        (CANCELADA, 'Cancelada'),
    ]

    titulo = models.CharField(max_length=200, verbose_name='Tema da reunião')
    pauta = models.TextField(blank=True, verbose_name='Pauta')
    inicio = models.DateTimeField(verbose_name='Início')
    fim = models.DateTimeField(
        null=True, blank=True, verbose_name='Fim previsto',
        help_text='Só uma previsão para a agenda. A sala não fecha na hora marcada.')

    organizador = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='reunioes_organizadas', verbose_name='Organizador')

    sala = models.CharField(max_length=64, unique=True, default=nova_sala,
                            verbose_name='Sala de vídeo')
    status = models.CharField(max_length=14, choices=STATUS, default=AGENDADA)
    gravar_ata = models.BooleanField(default=True, verbose_name='Gerar ata')

    evento = models.OneToOneField(
        'agenda.CalendarEvent', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reuniao', verbose_name='Evento na agenda')

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Reunião'
        verbose_name_plural = 'Reuniões'
        ordering = ['-inicio']
        indexes = [models.Index(fields=['inicio', 'status'])]

    def __str__(self):
        return f'{self.titulo} — {self.inicio:%d/%m/%Y %H:%M}'

    @property
    def acabou(self):
        return self.status in (self.ENCERRADA, self.CANCELADA)

    @property
    def ja_comecou(self):
        return self.inicio <= timezone.now()

    def convidado(self, user):
        return self.participantes.filter(user=user).exists()

    def pode_ver(self, user):
        if not (user and user.is_authenticated):
            return False
        return (user.is_superuser or self.organizador_id == user.id
                or self.convidado(user))

    def pode_editar(self, user):
        if not (user and user.is_authenticated):
            return False
        return user.is_superuser or self.organizador_id == user.id

    def destinatarios(self):
        """Todo mundo da agenda: convidados + organizador."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        ids = set(self.participantes.values_list('user_id', flat=True))
        ids.add(self.organizador_id)
        return User.objects.filter(id__in=ids, is_active=True)


class ParticipanteReuniao(models.Model):
    """Quem foi convidado, e por qual caminho entrou na lista."""

    MANUAL = 'MANUAL'
    CARGO = 'CARGO'
    SETOR = 'SETOR'
    GRUPO = 'GRUPO'
    COORDENACAO = 'COORDENACAO'
    ORIGENS = [
        (MANUAL, 'Escolhido na lista'),
        (CARGO, 'Pelo cargo'),
        (SETOR, 'Pelo setor'),
        (GRUPO, 'Pelo grupo'),
        (COORDENACAO, 'Pela coordenação'),
    ]

    reuniao = models.ForeignKey(Reuniao, on_delete=models.CASCADE,
                                related_name='participantes')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='reunioes_convidado')
    origem = models.CharField(max_length=12, choices=ORIGENS, default=MANUAL)
    rotulo_origem = models.CharField(
        max_length=120, blank=True, verbose_name='De onde veio',
        help_text='Nome do cargo/setor/grupo que trouxe a pessoa, para ela saber o porquê.')

    entrou_em = models.DateTimeField(null=True, blank=True, verbose_name='Entrou na sala')

    class Meta:
        verbose_name = 'Participante da reunião'
        verbose_name_plural = 'Participantes da reunião'
        unique_together = [('reuniao', 'user')]
        ordering = ['user__first_name', 'user__last_name']

    def __str__(self):
        return f'{self.user} em {self.reuniao_id}'

    @property
    def compareceu(self):
        return self.entrou_em is not None
