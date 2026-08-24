"""Posições das pessoas para o mapa.

A fonte principal é a marcação de ponto batida pelo portal
(``tangerino.RegistroPontoPortal``), que já guarda latitude e longitude — o
navegador pede a autorização à pessoa na hora de bater, então é dado coletado
com o conhecimento dela.

``PosicaoRegistrada`` existe para quando a empresa quiser registrar posição
fora do ponto (uma visita técnica, uma entrega). Cada registro guarda **de onde
veio** e **quem pediu**, para nunca se perder a resposta de "por que temos esta
localização".
"""
from django.conf import settings
from django.db import models


class PosicaoRegistrada(models.Model):
    """Uma posição enviada por alguém, fora do fluxo de ponto."""

    class Origem(models.TextChoices):
        PONTO = 'PONTO', 'Marcação de ponto'
        MANUAL = 'MANUAL', 'Envio manual'
        APP = 'APP', 'Aplicativo'

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='posicoes', verbose_name='Pessoa')
    latitude = models.FloatField(verbose_name='Latitude')
    longitude = models.FloatField(verbose_name='Longitude')
    precisao_metros = models.FloatField(
        null=True, blank=True, verbose_name='Precisão (m)',
        help_text='O que o navegador informou; ajuda a não tratar 2 km como se fosse 2 m.')
    momento = models.DateTimeField(verbose_name='Quando')
    origem = models.CharField(
        max_length=8, choices=Origem.choices, default=Origem.MANUAL, verbose_name='Origem')
    observacao = models.CharField(max_length=200, blank=True, verbose_name='Observação')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Posição registrada'
        verbose_name_plural = 'Posições registradas'
        ordering = ['-momento']
        indexes = [models.Index(fields=['usuario', '-momento'])]

    def __str__(self):
        return f'{self.usuario} em {self.momento:%d/%m/%Y %H:%M}'


class ConfiguracaoMapa(models.Model):
    """Interruptor da coleta de posição ao vivo.

    Nasce **desligada** de propósito. Ligar isto faz o portal pedir a posição
    do navegador de quem estiver logado, o que é um tipo de dado diferente do
    que o portal coleta hoje: o ponto registra onde a pessoa estava no momento
    em que ela mesma bateu; a coleta ao vivo acompanha onde ela está enquanto
    trabalha. Só ligue depois de comunicar a rede — a autorização do navegador
    é obrigatória, mas ela não substitui avisar as pessoas.
    """

    coleta_ativa = models.BooleanField(
        default=False, verbose_name='Coletar posição ao vivo',
        help_text='Desligado, o mapa mostra apenas a última marcação de ponto.')
    intervalo_minutos = models.PositiveSmallIntegerField(
        default=5, verbose_name='Intervalo de envio (min)',
        help_text='De quanto em quanto tempo o navegador reenvia a posição.')
    aviso = models.CharField(
        max_length=160, blank=True, default='Sua localização está sendo compartilhada com a gestão.',
        verbose_name='Aviso exibido à pessoa',
        help_text='Texto do selo que aparece no portal enquanto a posição é enviada.')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração do mapa'
        verbose_name_plural = 'Configuração do mapa'

    def __str__(self):
        return 'Coleta ativa' if self.coleta_ativa else 'Coleta desligada'

    @classmethod
    def carregar(cls):
        return cls.objects.first() or cls()
