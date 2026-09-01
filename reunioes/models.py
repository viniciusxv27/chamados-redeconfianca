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

    # Identidade visual da sala. O Jitsi roda num iframe de outro domínio: o
    # único jeito suportado de mudar logo, cores e fundo é o arquivo de
    # branding que ele busca sozinho (ver views.branding).
    fundo_sala_url = models.URLField(
        blank=True, verbose_name='Fundo de tela da sala',
        help_text='Deixe em branco para usar a imagem padrão da rede. '
                  'Precisa ser uma URL pública — quem baixa é o servidor de vídeo.')
    aplicar_fundo_padrao = models.BooleanField(
        default=True, verbose_name='Aplicar o fundo automaticamente',
        help_text='Liga o fundo virtual para todo mundo ao entrar. Desligue se '
                  'os celulares da rede ficarem lentos: o efeito processa vídeo.')

    permitir_link_publico = models.BooleanField(
        default=True, verbose_name='Permitir link público de visitante',
        help_text='Deixa o organizador gerar um link para quem não tem conta no '
                  'portal. Desligar aqui derruba os links já criados.')

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


def novo_token_publico():
    """O link de visitante é o segredo: quem tem, entra."""
    return secrets.token_urlsafe(24)


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

    # Link de visitante. Vazio = desligado, e é assim que toda reunião nasce:
    # quem abre a porta para fora é o organizador, numa ação explícita.
    token_publico = models.CharField(
        max_length=64, blank=True, db_index=True, verbose_name='Link público')
    publico_em = models.DateTimeField(null=True, blank=True,
                                      verbose_name='Link público criado em')

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

    # ---- link público -----------------------------------------------------
    def abrir_link_publico(self):
        """Gera (ou troca) o token. Trocar invalida o link antigo na hora."""
        self.token_publico = novo_token_publico()
        self.publico_em = timezone.now()
        self.save(update_fields=['token_publico', 'publico_em', 'atualizado_em'])
        return self.token_publico

    def fechar_link_publico(self):
        self.token_publico = ''
        self.publico_em = None
        self.save(update_fields=['token_publico', 'publico_em', 'atualizado_em'])

    @property
    def tem_link_publico(self):
        return bool(self.token_publico)

    def visitante_pode_entrar(self):
        """O link só vale enquanto a reunião existe de fato.

        Reunião cancelada ou encerrada com link esquecido aberto seria uma sala
        de vídeo pública sem dono — fecha sozinha.
        """
        if not self.token_publico or self.acabou:
            return False
        return ConfiguracaoReunioes.get().permitir_link_publico

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


class VisitanteReuniao(models.Model):
    """Quem entrou pelo link público.

    Fica registrado porque numa ata "quem estava na reunião" precisa incluir
    quem veio de fora. Guarda só o nome digitado e a hora — nada de IP: para
    saber quem entrou basta o nome, e o resto seria coleta sem uso.
    """

    reuniao = models.ForeignKey(Reuniao, on_delete=models.CASCADE,
                                related_name='visitantes')
    nome = models.CharField(max_length=80, verbose_name='Nome informado')
    entrou_em = models.DateTimeField(auto_now_add=True, verbose_name='Entrou em')

    class Meta:
        verbose_name = 'Visitante da reunião'
        verbose_name_plural = 'Visitantes da reunião'
        ordering = ['entrou_em']

    def __str__(self):
        return f'{self.nome} (visitante) em {self.reuniao_id}'
