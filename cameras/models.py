"""Câmeras das lojas: gravadores, canais e alertas de possível furto.

O vídeo não passa pelo banco: o agente instalado na loja (pasta agente_cameras) manda fotos curtas,
que ficam só no cache, e os alertas. Alerta é suspeita para uma pessoa conferir, nunca acusação.
"""
import hashlib
import secrets
import uuid
from datetime import time, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from webmail.seguranca import cifrar, decifrar

MINUTOS_ONLINE = 3
DIAS_DO_CODIGO = 7
VERSAO_COM_AO_VIVO = (2, 1)


class ConfigCameras(models.Model):
    """Uma linha só. Nasce desligado: a loja só começa a mandar imagem depois do aviso à rede."""
    ativo = models.BooleanField(default=False, verbose_name='Receber imagens e alertas das lojas')
    alterado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    # O .exe publicado por manage.py publicar_instalador_cameras: é o mesmo para todas as lojas.
    instalador_arquivo = models.CharField(max_length=200, blank=True, db_default='', editable=False)
    instalador_em = models.DateTimeField(null=True, blank=True, editable=False)
    # Versão do agente dentro do .exe publicado, e a impressão digital que o agente confere antes de se atualizar.
    instalador_versao = models.CharField(max_length=20, blank=True, db_default='', editable=False)
    instalador_sha256 = models.CharField(max_length=64, blank=True, db_default='', editable=False)
    instalador_tamanho = models.PositiveBigIntegerField(default=0, db_default=0, editable=False)
    # Nasce desligado: com ele ligado, o agente da loja baixa e instala sozinho a versão publicada.
    atualizar_agentes = models.BooleanField(default=False, db_default=False, verbose_name='Atualizar o agente das lojas sozinho')
    # Avisos fora da tela (WhatsApp/notificação). Nascem desligados.
    aviso_whatsapp = models.BooleanField(default=False, db_default=False, verbose_name='Avisar alerta importante no WhatsApp')
    aviso_prioridade = models.PositiveSmallIntegerField(default=60, db_default=60, verbose_name='Avisar a partir da prioridade')
    aviso_destino = models.CharField(max_length=120, blank=True, db_default='', verbose_name='WhatsApp que recebe os avisos')
    aviso_offline = models.BooleanField(default=False, db_default=False, verbose_name='Avisar loja sem contato')
    aviso_offline_minutos = models.PositiveSmallIntegerField(default=30, db_default=30, verbose_name='Sem contato há (min)')
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

    # O matinal: gente reunida na área da tela, dentro deste horário, por pelo menos este tempo.
    matinal_inicio = models.TimeField(default=time(5, 0), db_default=time(5, 0), verbose_name='Matinal a partir de')
    matinal_fim = models.TimeField(default=time(9, 0), db_default=time(9, 0), verbose_name='Matinal antes de')
    matinal_minutos = models.PositiveSmallIntegerField(default=5, db_default=5, verbose_name='Matinal dura ao menos (min)')
    # Equipe chegando antes de abrir e fechando depois do horário não é "pessoa com a loja fechada".
    tolerancia_horario = models.PositiveSmallIntegerField(default=60, db_default=60,
                                                          verbose_name='Tolerância antes de abrir e depois de fechar (min)')
    # Quais alertas a loja gera. Permanência nasce desligada: em loja de telefonia a equipe fica no mostruário o dia todo.
    alerta_fora_horario = models.BooleanField(default=True, db_default=True, verbose_name='Alertar pessoa com a loja fechada')
    alerta_permanencia = models.BooleanField(default=False, db_default=False, verbose_name='Alertar permanência longa na área')

    # O código no nome do instalador baixado: vira o token da loja quando o instalador roda.
    codigo_hash = models.CharField(max_length=64, blank=True, db_default='', editable=False, db_index=True)
    codigo_validade = models.DateTimeField(null=True, blank=True, editable=False)
    token_hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)
    ultimo_contato = models.DateTimeField(null=True, blank=True, editable=False)
    versao_agente = models.CharField(max_length=30, blank=True, editable=False)
    # Diagnóstico à distância: como está o vídeo ao vivo e o fim do log do computador da loja.
    ao_vivo_situacao = models.CharField(max_length=200, blank=True, db_default='', editable=False)
    log_agente = models.TextField(blank=True, db_default='', editable=False)
    log_agente_em = models.DateTimeField(null=True, blank=True, editable=False)
    # O que o agente achou sozinho na rede da loja, e o motivo quando não achou.
    serial_detectado = models.CharField(max_length=60, blank=True, db_default='', editable=False)
    modelo_detectado = models.CharField(max_length=80, blank=True, db_default='', editable=False)
    erro_agente = models.CharField(max_length=255, blank=True, db_default='', editable=False)
    descoberta_em = models.DateTimeField(null=True, blank=True, editable=False)
    # Saúde que o agente manda no sinal (câmeras com imagem, quadros por segundo, detector, filas, disco...).
    saude = models.JSONField(default=dict, blank=True, db_default={}, editable=False)
    saude_em = models.DateTimeField(null=True, blank=True, editable=False)
    # Último aviso de "loja sem contato", para não repetir a cada minuto.
    aviso_offline_em = models.DateTimeField(null=True, blank=True, editable=False)
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

    def gerar_codigo(self):
        """Sem letras e números que se confundem. O token só muda quando o instalador roda: baixar não derruba ninguém."""
        codigo = ''.join(secrets.choice('ABCDEFGHJKMNPQRSTUVWXYZ23456789') for _ in range(12))
        self.codigo_hash = self.resumo(codigo)
        self.codigo_validade = timezone.now() + timedelta(days=DIAS_DO_CODIGO)
        self.save(update_fields=['codigo_hash', 'codigo_validade'])
        return codigo

    @staticmethod
    def resumo(token):
        return hashlib.sha256((token or '').encode()).hexdigest()

    @property
    def online(self):
        return bool(self.ultimo_contato and (timezone.now() - self.ultimo_contato).total_seconds() < MINUTOS_ONLINE * 60)

    @property
    def tem_ao_vivo(self):
        try:
            return tuple(int(p) for p in self.versao_agente.split('.')[:2]) >= VERSAO_COM_AO_VIVO
        except ValueError:
            return False

    @property
    def serial_diferente(self):
        return bool(self.serial_detectado and self.serial and self.serial_detectado.upper() != self.serial.upper())

    def alerta_ligado(self, tipo):
        if tipo == AlertaCamera.Tipo.FORA_HORARIO:
            return self.alerta_fora_horario
        if tipo == AlertaCamera.Tipo.PERMANENCIA:
            return self.alerta_permanencia
        return True

    def na_tolerancia(self, momento):
        """O momento (local) cai na margem antes de abrir ou depois de fechar: equipe abrindo ou fechando a loja."""
        local = timezone.localtime(momento)
        minutos = local.hour * 60 + local.minute
        abre = self.abre_as.hour * 60 + self.abre_as.minute
        fecha = self.fecha_as.hour * 60 + self.fecha_as.minute
        margem = self.tolerancia_horario or 0
        return abre - margem <= minutos < abre or fecha <= minutos < fecha + margem

    @property
    def saude_atual(self):
        """A saúde que o agente mandou, só se for recente (senão é retrato velho)."""
        if not self.saude_em or (timezone.now() - self.saude_em).total_seconds() > MINUTOS_ONLINE * 60 * 2:
            return {}
        return self.saude or {}

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


def _caminho_video(instancia, nome):
    agora = timezone.now()
    return f'cameras/clipes/{agora:%Y/%m}/{uuid.uuid4().hex}.mp4'


def _caminho_quadro(instancia, nome):
    agora = timezone.now()
    return f'cameras/quadros/{agora:%Y/%m}/{uuid.uuid4().hex}.jpg'


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

    class MotivoFalso(models.TextChoices):
        """Por que foi falso: é o que a IA mais aprende de cada decisão."""
        FUNCIONARIO = 'FUNCIONARIO', 'Funcionário trabalhando'
        ATENDIMENTO = 'ATENDIMENTO', 'Atendimento normal / cliente devolveu'
        DETECCAO = 'DETECCAO', 'A IA viu errado (sombra, reflexo, objeto)'
        LIMPEZA = 'LIMPEZA', 'Limpeza, reposição ou manutenção'
        OUTRO = 'OUTRO', 'Outro'

    class Video(models.TextChoices):
        SEM_VIDEO = 'SEM_VIDEO', 'Sem vídeo'
        AGUARDANDO = 'AGUARDANDO', 'Gravando na loja'
        RECEBIDO = 'RECEBIDO', 'Vídeo recebido'
        APAGADO = 'APAGADO', 'Apagado pelo prazo'

    class Ia(models.TextChoices):
        PENDENTE = 'PENDENTE', 'Analisando'
        FEITA = 'FEITA', 'Analisado'
        ERRO = 'ERRO', 'Não deu para analisar'

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

    motivo_falso = models.CharField(max_length=12, choices=MotivoFalso.choices, blank=True, default='', db_default='')
    # O que o agente mediu no momento (pessoas, bolsa, tempo perto, nota do detector...).
    detalhes = models.JSONField(default=dict, blank=True)
    # Os 3 minutos em volta do acontecimento (1min30 antes e depois), gravados na loja.
    video = models.FileField(upload_to=_caminho_video, blank=True, max_length=200)
    video_status = models.CharField(max_length=10, choices=Video.choices, default=Video.SEM_VIDEO, db_default='SEM_VIDEO')
    video_inicio = models.DateTimeField(null=True, blank=True)
    video_segundos = models.PositiveIntegerField(default=0, db_default=0)
    video_evento_segundo = models.PositiveIntegerField(default=0, db_default=0, help_text='Em que segundo do vídeo está o acontecimento.')
    video_tamanho = models.PositiveBigIntegerField(default=0, db_default=0)
    # Quadros-chave do vídeo: [{"arquivo": caminho, "segundo": -30}] (segundo em relação ao acontecimento).
    quadros = models.JSONField(default=list, blank=True)
    # A segunda olhada, com IA de visão sobre os quadros-chave.
    ia_status = models.CharField(max_length=10, choices=Ia.choices, blank=True, default='', db_default='')
    ia_risco = models.PositiveSmallIntegerField(null=True, blank=True)
    ia_classificacao = models.CharField(max_length=20, blank=True, default='', db_default='')
    ia_resumo = models.TextField(blank=True, default='', db_default='')
    ia_analise = models.JSONField(default=dict, blank=True)
    ia_modelo = models.CharField(max_length=40, blank=True, default='', db_default='')
    ia_erro = models.CharField(max_length=200, blank=True, default='', db_default='')
    ia_em = models.DateTimeField(null=True, blank=True)
    # O que as decisões anteriores (confirmado/falso) dizem de alertas parecidos, em %.
    chance_real = models.PositiveSmallIntegerField(null=True, blank=True)
    prioridade = models.PositiveSmallIntegerField(default=0, db_default=0, db_index=True)
    # Quando saiu o aviso fora da tela (WhatsApp/notificação): avisa uma vez só.
    avisado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Alerta de câmera'
        verbose_name_plural = 'Alertas de câmera'
        ordering = ['-criado_em']


class EventoLoja(models.Model):
    """O que o agente mediu. Só contagem e tempo: sem imagem e sem identificar ninguém."""

    class Tipo(models.TextChoices):
        ENTRADA = 'ENTRADA', 'Entrada pela porta'
        ATENDIMENTO = 'ATENDIMENTO', 'Tempo na mesa'
        MATINAL = 'MATINAL', 'Matinal'
        COBERTURA = 'COBERTURA', 'Câmera analisando nesta hora'

    gravador = models.ForeignKey(Gravador, on_delete=models.CASCADE, related_name='eventos')
    canal = models.ForeignKey(Canal, on_delete=models.SET_NULL, null=True, blank=True, related_name='eventos')
    chave = models.CharField(max_length=32)
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    zona = models.CharField(max_length=40, blank=True)
    inicio = models.DateTimeField()
    fim = models.DateTimeField()
    data = models.DateField()
    hora = models.PositiveSmallIntegerField()
    duracao = models.PositiveIntegerField(default=0, verbose_name='Segundos')
    pessoas = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = 'Medição da loja'
        verbose_name_plural = 'Medições das lojas'
        ordering = ['-fim']
        constraints = [models.UniqueConstraint(fields=['gravador', 'chave'], name='cameras_evento_uma_vez')]
        indexes = [models.Index(fields=['data', 'tipo', 'gravador'], name='cameras_evento_dia_idx')]


class SessaoAoVivo(models.Model):
    """Um pedido de vídeo ao vivo. O portal só apresenta os dois lados: o vídeo vai direto da loja ao navegador."""
    BAIXA, ALTA = 'BAIXA', 'ALTA'
    PEDIDA, ENTREGUE, RESPONDIDA, CONECTADA, FALHOU, FECHADA = 'PEDIDA', 'ENTREGUE', 'RESPONDIDA', 'CONECTADA', 'FALHOU', 'FECHADA'
    ESTADOS = [(PEDIDA, 'Pedida'), (ENTREGUE, 'Entregue ao agente'), (RESPONDIDA, 'Respondida'), (CONECTADA, 'Conectada'),
               (FALHOU, 'Falhou'), (FECHADA, 'Fechada')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    gravador = models.ForeignKey(Gravador, on_delete=models.CASCADE, related_name='sessoes_ao_vivo')
    canal = models.ForeignKey(Canal, on_delete=models.CASCADE, related_name='sessoes_ao_vivo')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    qualidade = models.CharField(max_length=5, default=BAIXA)
    oferta = models.TextField(blank=True)
    resposta = models.TextField(blank=True)
    erro = models.CharField(max_length=200, blank=True)
    codec = models.CharField(max_length=20, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default=PEDIDA, db_index=True)
    criada_em = models.DateTimeField(auto_now_add=True, db_index=True)
    atualizada_em = models.DateTimeField(auto_now=True)
    # O estado final apaga o caminho: estes guardam até onde a sessão chegou.
    entregue_em = models.DateTimeField(null=True, blank=True)
    respondida_em = models.DateTimeField(null=True, blank=True)
    conectada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Sessão de vídeo ao vivo'
        verbose_name_plural = 'Sessões de vídeo ao vivo'
        ordering = ['-criada_em']
        indexes = [models.Index(fields=['gravador', 'estado'], name='cameras_sessao_grav_idx')]


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
