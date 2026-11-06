"""Solicitações ao Departamento Pessoal: ajuste de ponto, atestados e declarações.

O colaborador pede pelo portal (com os horários, a justificativa e o documento); o DP
recebe o aviso, analisa, aprova ou devolve para correção. Tudo fica no histórico:
quem fez o quê, quando, e o que mudou em cada reenvio.
"""
import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def _storage():
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage()
    return None


def _caminho_anexo(instancia, nome):
    # Nome impossível de adivinhar: atestado é dado de saúde, e o arquivo só sai pela view com permissão.
    extensao = os.path.splitext(nome or '')[1].lower()[:8]
    return f'dp/solicitacoes/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{extensao}'


class ResponsavelDP(models.Model):
    """Quem recebe os avisos e analisa as solicitações (além do RH/DP e do SUPERADMIN)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='responsavel_dp')
    recebe_avisos = models.BooleanField(default=True, verbose_name='Recebe os avisos de novas solicitações')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Responsável do DP'
        verbose_name_plural = 'Responsáveis do DP'

    def __str__(self):
        return str(self.user)


class Solicitacao(models.Model):
    AJUSTE, ATESTADO, DECLARACAO = 'AJUSTE', 'ATESTADO', 'DECLARACAO'
    TIPOS = [(AJUSTE, 'Ajuste de ponto'), (ATESTADO, 'Atestado'), (DECLARACAO, 'Declaração')]

    ENVIADA, EM_ANALISE, DEVOLVIDA, APROVADA, CANCELADA = 'ENVIADA', 'EM_ANALISE', 'DEVOLVIDA', 'APROVADA', 'CANCELADA'
    STATUS = [(ENVIADA, 'Aguardando o DP'), (EM_ANALISE, 'Em análise'), (DEVOLVIDA, 'Devolvida para correção'),
              (APROVADA, 'Aprovada'), (CANCELADA, 'Cancelada')]
    ABERTOS = (ENVIADA, EM_ANALISE, DEVOLVIDA)

    MOTIVOS_AJUSTE = [
        ('ESQUECEU', 'Esqueci de bater o ponto'),
        ('NAO_REGISTROU', 'O relógio ou o aplicativo não registrou'),
        ('HORARIO_ERRADO', 'Bati no horário errado'),
        ('EXTERNO', 'Trabalho externo / fora da loja'),
        ('OUTRO', 'Outro motivo'),
    ]
    ESPECIES_ATESTADO = [
        ('MEDICO', 'Atestado médico'),
        ('ODONTOLOGICO', 'Atestado odontológico'),
        ('ACOMPANHAMENTO', 'Acompanhamento de familiar'),
        ('OUTRO_ATESTADO', 'Outro atestado'),
    ]
    ESPECIES_DECLARACAO = [
        ('COMPARECIMENTO', 'Comparecimento a consulta ou exame'),
        ('ESCOLAR', 'Escolar / prova / vestibular'),
        ('ORGAO_PUBLICO', 'Justiça, cartório ou órgão público'),
        ('DOACAO_SANGUE', 'Doação de sangue'),
        ('OUTRA_DECLARACAO', 'Outra declaração'),
    ]
    ESPECIES = ESPECIES_ATESTADO + ESPECIES_DECLARACAO

    colaborador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='solicitacoes_dp')
    tipo = models.CharField(max_length=12, choices=TIPOS, db_index=True)
    status = models.CharField(max_length=12, choices=STATUS, default=ENVIADA, db_index=True)
    especie = models.CharField('Tipo de documento', max_length=20, choices=ESPECIES, blank=True)
    data = models.DateField('Dia', db_index=True)
    data_fim = models.DateField('Até', null=True, blank=True)
    hora_inicio = models.TimeField('Das', null=True, blank=True)
    hora_fim = models.TimeField('Até as', null=True, blank=True)
    motivo_ajuste = models.CharField('O que aconteceu', max_length=20, choices=MOTIVOS_AJUSTE, blank=True)
    # Ajuste: os horários certos do dia, em ordem. [{"rotulo": "Entrada", "hora": "08:00"}]
    marcacoes = models.JSONField(default=list, blank=True)
    justificativa = models.TextField('Justificativa', max_length=2000)

    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='solicitacoes_dp_analisadas', verbose_name='Quem do DP analisa')
    resposta = models.TextField('Última resposta do DP', blank=True)
    ajustado_no_ponto = models.BooleanField('Ajuste lançado no ponto', default=False)
    ajustado_em = models.DateTimeField(null=True, blank=True)
    aprovada_em = models.DateTimeField(null=True, blank=True)
    # Atestado/declaração aprovado vai para os documentos do colaborador (categoria certa).
    documentos = models.ManyToManyField('users.UserDocument', blank=True, related_name='+')

    enviada_em = models.DateTimeField(default=timezone.now, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-enviada_em']
        verbose_name = 'Solicitação ao DP'
        verbose_name_plural = 'Solicitações ao DP'
        indexes = [models.Index(fields=['status', 'tipo'])]

    def __str__(self):
        return f'#{self.pk} {self.get_tipo_display()} · {self.colaborador} · {self.data:%d/%m/%Y}'

    @property
    def aberta(self):
        return self.status in self.ABERTOS

    @property
    def dias(self):
        if self.tipo == self.AJUSTE or not self.data_fim:
            return 1
        return (self.data_fim - self.data).days + 1

    @property
    def periodo(self):
        texto = f'{self.data:%d/%m/%Y}'
        if self.data_fim and self.data_fim != self.data:
            texto += f' a {self.data_fim:%d/%m/%Y}'
        if self.hora_inicio and self.hora_fim:
            texto += f', das {self.hora_inicio:%H:%M} às {self.hora_fim:%H:%M}'
        return texto

    @property
    def horarios_texto(self):
        return ' · '.join(f'{m.get("rotulo", "")} {m.get("hora", "")}'.strip() for m in self.marcacoes or [])

    @property
    def titulo(self):
        if self.tipo == self.AJUSTE:
            return f'Ajuste de ponto de {self.data:%d/%m/%Y}'
        return f'{self.get_especie_display() or self.get_tipo_display()} · {self.periodo}'

    @property
    def dias_esperando(self):
        return (timezone.now() - self.enviada_em).days


class Anexo(models.Model):
    solicitacao = models.ForeignKey(Solicitacao, on_delete=models.CASCADE, related_name='anexos')
    arquivo = models.FileField(upload_to=_caminho_anexo, storage=_storage(), max_length=300)
    nome_original = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    tamanho = models.PositiveIntegerField(default=0)
    enviado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    enviado_em = models.DateTimeField(auto_now_add=True)
    # Tirado na correção: some da solicitação, mas fica no histórico (e no storage) para o registro.
    removido = models.BooleanField(default=False)

    class Meta:
        ordering = ['enviado_em', 'id']

    @property
    def extensao(self):
        return os.path.splitext(self.nome_original or self.arquivo.name or '')[1].lower()

    @property
    def e_imagem(self):
        return self.extensao in ('.jpg', '.jpeg', '.png', '.webp', '.gif')

    @property
    def e_pdf(self):
        return self.extensao == '.pdf'


class Evento(models.Model):
    """Histórico: cada passo da solicitação, com o que mudou."""
    CRIADA, REENVIADA, EM_ANALISE, APROVADA, DEVOLVIDA = 'CRIADA', 'REENVIADA', 'EM_ANALISE', 'APROVADA', 'DEVOLVIDA'
    CANCELADA, COMENTARIO, AJUSTADA = 'CANCELADA', 'COMENTARIO', 'AJUSTADA'
    ACOES = [(CRIADA, 'Enviou a solicitação'), (REENVIADA, 'Corrigiu e reenviou'), (EM_ANALISE, 'Começou a análise'),
             (APROVADA, 'Aprovou'), (DEVOLVIDA, 'Devolveu para correção'), (CANCELADA, 'Cancelou'),
             (COMENTARIO, 'Comentou'), (AJUSTADA, 'Lançou o ajuste no ponto')]

    solicitacao = models.ForeignKey(Solicitacao, on_delete=models.CASCADE, related_name='eventos')
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    acao = models.CharField(max_length=12, choices=ACOES)
    texto = models.TextField(blank=True)
    # [{"campo": "Horários", "antes": "...", "depois": "..."}]
    alteracoes = models.JSONField(default=list, blank=True)
    em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['em', 'id']
