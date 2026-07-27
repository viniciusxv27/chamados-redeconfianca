"""Modelos do módulo IMPULSO (gestão administrativa dentro do portal).

Estrutura (conforme especificação):
  - CONFIAR:        Meta, MetaAnexo, MetaComentario, ImpulsoFeedback
  - CONECTAR:       ConteudoConectar, ConclusaoConteudo, ProjetoFoco, TarefaProjeto
  - INOVAR:         Ideia
  - ACOMPANHAMENTO: cálculo de faixas (ver impulso/utils.py)

Visibilidade do módulo é restrita ao CommunicationGroup "ESCRITÓRIO (ADM)".
O papel de gestor é definido pelo CommunicationGroup "GESTORES (IMPULSO)".
Ambos gerenciados em /users/manage/groups/.
"""
import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


# Nomes dos grupos (CommunicationGroup) que controlam acesso e papel.
GRUPO_ADM = 'ESCRITÓRIO (ADM)'
GRUPO_GESTOR = 'GESTORES (IMPULSO)'


def get_media_storage():
    """Retorna o storage de mídia (MinIO/S3 quando USE_S3, senão o padrão).

    Segue o padrão dos demais apps: retorna a classe MediaStorage (S3) ou None
    (usa o DEFAULT_FILE_STORAGE local). Chame com parênteses no campo:
    ``storage=get_media_storage()``.
    """
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


def _uuid_name(filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"{uuid.uuid4().hex}{ext}"


def upload_meta_anexo(instance, filename):
    return f"impulso/metas/{instance.meta_id or 'novos'}/{_uuid_name(filename)}"


def upload_conteudo(instance, filename):
    return f"impulso/conectar/{instance.tipo.lower()}/{_uuid_name(filename)}"


def upload_certificado(instance, filename):
    return f"impulso/certificados/{instance.user_id}/{_uuid_name(filename)}"


# ==========================================================================
# CONFIAR
# ==========================================================================
class Meta(models.Model):
    """Meta/tarefa atribuída por um gestor a um colaborador.

    Aparece no Kanban, aceita anexos (arquivo/link) e chat de comentários.
    Ao concluir, o gestor faz o "check" com notas 0-5 de qualidade e prazo.
    """

    class Periodicidade(models.TextChoices):
        SEMANAL = 'SEMANAL', 'Semanal'
        QUINZENAL = 'QUINZENAL', 'Quinzenal'
        MENSAL = 'MENSAL', 'Mensal'

    class Status(models.TextChoices):
        A_FAZER = 'A_FAZER', 'A Fazer'
        EM_ANDAMENTO = 'EM_ANDAMENTO', 'Em Andamento'
        ENTREGUE = 'ENTREGUE', 'Entregue (aguardando avaliação)'
        CONCLUIDA = 'CONCLUIDA', 'Concluída'

    # Colunas exibidas no Kanban, na ordem.
    KANBAN_STATUSES = [Status.A_FAZER, Status.EM_ANDAMENTO, Status.ENTREGUE, Status.CONCLUIDA]

    gestor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='impulso_metas_gerenciadas', verbose_name='Gestor')
    colaborador = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='impulso_metas', verbose_name='Colaborador')

    titulo = models.CharField(max_length=200, verbose_name='Título da meta')
    descricao = models.TextField(
        verbose_name='Descrição',
        help_text='Descreva a meta de forma clara, por textos.')
    periodicidade = models.CharField(
        max_length=12, choices=Periodicidade.choices,
        default=Periodicidade.MENSAL, verbose_name='Periodicidade')
    prazo = models.DateField(verbose_name='Prazo')

    status = models.CharField(
        max_length=14, choices=Status.choices,
        default=Status.A_FAZER, verbose_name='Status')
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem no Kanban')

    # Entrega do colaborador
    entrega_link = models.URLField(
        blank=True, verbose_name='Link da entrega',
        help_text='Link da tarefa ou projeto entregue (opcional).')
    entregue_em = models.DateTimeField(null=True, blank=True, verbose_name='Entregue em')

    # Check do gestor (retorno após concluído)
    nota_qualidade = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name='Qualidade de entrega (0-5)')
    nota_prazo = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name='Cumprimento de prazo (0-5)')
    avaliacao_comentario = models.TextField(blank=True, verbose_name='Comentário do gestor')
    avaliado_em = models.DateTimeField(null=True, blank=True, verbose_name='Avaliado em')
    avaliado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_metas_avaliadas', verbose_name='Avaliado por')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='impulso_metas_criadas', verbose_name='Criado por')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Meta'
        verbose_name_plural = 'Metas'
        ordering = ['order', '-created_at']

    def __str__(self):
        return f"{self.titulo} — {self.colaborador.get_full_name() or self.colaborador.username}"

    @property
    def is_avaliada(self):
        return self.nota_qualidade is not None and self.nota_prazo is not None

    @property
    def media_avaliacao(self):
        """Média das duas notas (escala 0-5), ou None se não avaliada."""
        if not self.is_avaliada:
            return None
        return round((self.nota_qualidade + self.nota_prazo) / 2, 1)

    @property
    def is_overdue(self):
        if self.status == self.Status.CONCLUIDA or not self.prazo:
            return False
        return self.prazo < timezone.localdate()


class MetaAnexo(models.Model):
    """Anexo de uma meta: arquivo enviado OU link externo."""

    class Tipo(models.TextChoices):
        ARQUIVO = 'ARQUIVO', 'Arquivo'
        LINK = 'LINK', 'Link'

    meta = models.ForeignKey(
        Meta, on_delete=models.CASCADE, related_name='anexos', verbose_name='Meta')
    tipo = models.CharField(max_length=8, choices=Tipo.choices, verbose_name='Tipo')
    titulo = models.CharField(max_length=200, blank=True, verbose_name='Título')
    arquivo = models.FileField(
        upload_to=upload_meta_anexo, storage=get_media_storage(),
        null=True, blank=True, verbose_name='Arquivo')
    url = models.URLField(blank=True, verbose_name='URL')

    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='impulso_anexos_enviados', verbose_name='Enviado por')
    enviado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Anexo de Meta'
        verbose_name_plural = 'Anexos de Metas'
        ordering = ['-enviado_em']

    def __str__(self):
        return self.titulo or (self.url or (self.arquivo.name if self.arquivo else 'Anexo'))

    @property
    def nome_exibicao(self):
        if self.titulo:
            return self.titulo
        if self.tipo == self.Tipo.LINK:
            return self.url
        return os.path.basename(self.arquivo.name) if self.arquivo else 'Arquivo'


class MetaComentario(models.Model):
    """Comentário/chat em uma meta (atividade/tarefa)."""

    meta = models.ForeignKey(
        Meta, on_delete=models.CASCADE, related_name='comentarios', verbose_name='Meta')
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='impulso_comentarios', verbose_name='Autor')
    mensagem = models.TextField(verbose_name='Mensagem')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Comentário de Meta'
        verbose_name_plural = 'Comentários de Metas'
        ordering = ['criado_em']

    def __str__(self):
        return f"Comentário de {self.autor} em {self.meta_id}"


class ImpulsoFeedback(models.Model):
    """Feedback mensal do gestor para o colaborador, com resumo gerado por IA."""

    gestor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='impulso_feedbacks_dados', verbose_name='Gestor')
    colaborador = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='impulso_feedbacks_recebidos', verbose_name='Colaborador')

    referencia_mes = models.DateField(
        verbose_name='Mês de referência',
        help_text='Primeiro dia do mês de referência do feedback.')

    pontos_fortes = models.TextField(verbose_name='Pontos fortes')
    pontos_melhoria = models.TextField(verbose_name='Pontos a melhorar')
    comentario = models.TextField(blank=True, verbose_name='Comentário geral')

    # Resumo IA (padrão reaproveitado de feedback/ai.py)
    ai_summary = models.TextField(blank=True, verbose_name='Resumo IA')
    ai_summary_generated_at = models.DateTimeField(null=True, blank=True)
    ai_summary_error = models.TextField(blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Feedback (Impulso)'
        verbose_name_plural = 'Feedbacks (Impulso)'
        ordering = ['-referencia_mes', '-criado_em']

    def __str__(self):
        return f"Feedback {self.referencia_mes:%m/%Y} — {self.colaborador}"


# ==========================================================================
# CONECTAR
# ==========================================================================
class ConteudoConectar(models.Model):
    """Curso, vídeo ou POP sinalizado no bloco CONECTAR.

    Gestores sobem cursos/vídeos/POPs necessários; a equipe visualiza,
    conclui e anexa certificado. POPs também podem ser enviados pela equipe.
    """

    class Tipo(models.TextChoices):
        CURSO = 'CURSO', 'Curso'
        VIDEO = 'VIDEO', 'Vídeo'
        POP = 'POP', 'POP'

    tipo = models.CharField(max_length=6, choices=Tipo.choices, verbose_name='Tipo')
    titulo = models.CharField(max_length=200, verbose_name='Título')
    descricao = models.TextField(blank=True, verbose_name='Descrição')

    arquivo = models.FileField(
        upload_to=upload_conteudo, storage=get_media_storage(),
        null=True, blank=True, verbose_name='Arquivo',
        help_text='Vídeo, PDF do POP ou material do curso (opcional).')
    url = models.URLField(blank=True, verbose_name='Link externo',
                          help_text='Link do curso/vídeo (opcional).')

    obrigatorio = models.BooleanField(default=True, verbose_name='Obrigatório')
    obrigatorio_para = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name='impulso_conteudos_obrigatorios',
        verbose_name='Obrigatório para',
        help_text='Se vazio, vale para toda a equipe do Escritório (ADM).')
    inicio = models.DateField(null=True, blank=True, verbose_name='Início do período')
    fim = models.DateField(null=True, blank=True, verbose_name='Fim do período')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='impulso_conteudos_criados', verbose_name='Criado por')
    criado_por_equipe = models.BooleanField(
        default=False, verbose_name='Enviado pela equipe',
        help_text='POP enviado por um membro da equipe (não gestor).')
    criado_em = models.DateTimeField(auto_now_add=True)
    ativo = models.BooleanField(default=True, verbose_name='Ativo')

    class Meta:
        verbose_name = 'Conteúdo CONECTAR'
        verbose_name_plural = 'Conteúdos CONECTAR'
        ordering = ['-criado_em']

    def __str__(self):
        return f"[{self.get_tipo_display()}] {self.titulo}"

    def periodo_ativo(self):
        hoje = timezone.localdate()
        if self.inicio and hoje < self.inicio:
            return False
        if self.fim and hoje > self.fim:
            return False
        return True


class ConclusaoConteudo(models.Model):
    """Conclusão de um conteúdo por um usuário, com certificado anexado."""

    conteudo = models.ForeignKey(
        ConteudoConectar, on_delete=models.CASCADE,
        related_name='conclusoes', verbose_name='Conteúdo')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='impulso_conclusoes', verbose_name='Usuário')
    concluido = models.BooleanField(default=False, verbose_name='Concluído')
    certificado = models.FileField(
        upload_to=upload_certificado, storage=get_media_storage(),
        null=True, blank=True, verbose_name='Certificado')
    concluido_em = models.DateTimeField(null=True, blank=True, verbose_name='Concluído em')

    class Meta:
        verbose_name = 'Conclusão de Conteúdo'
        verbose_name_plural = 'Conclusões de Conteúdo'
        unique_together = ('conteudo', 'user')
        ordering = ['-concluido_em']

    def __str__(self):
        return f"{self.user} — {self.conteudo} ({'ok' if self.concluido else 'pendente'})"


class ProjetoFoco(models.Model):
    """Projeto foco: o gestor adequa a equipe e as atribuições (tarefas)."""

    nome = models.CharField(max_length=200, verbose_name='Nome do projeto')
    descricao = models.TextField(blank=True, verbose_name='Descrição')
    membros = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name='impulso_projetos_foco', verbose_name='Equipe do projeto')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='impulso_projetos_criados', verbose_name='Criado por')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Projeto Foco'
        verbose_name_plural = 'Projetos Foco'
        ordering = ['-criado_em']

    def __str__(self):
        return self.nome


class TarefaProjeto(models.Model):
    """Tarefa de um projeto foco, destinada a um usuário."""

    class Status(models.TextChoices):
        A_FAZER = 'A_FAZER', 'A Fazer'
        EM_ANDAMENTO = 'EM_ANDAMENTO', 'Em Andamento'
        CONCLUIDA = 'CONCLUIDA', 'Concluída'

    projeto = models.ForeignKey(
        ProjetoFoco, on_delete=models.CASCADE,
        related_name='tarefas', verbose_name='Projeto')
    titulo = models.CharField(max_length=200, verbose_name='Título')
    descricao = models.TextField(blank=True, verbose_name='Descrição')
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_tarefas_projeto', verbose_name='Responsável')
    prazo = models.DateField(null=True, blank=True, verbose_name='Prazo')
    status = models.CharField(
        max_length=14, choices=Status.choices,
        default=Status.A_FAZER, verbose_name='Status')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='impulso_tarefas_criadas', verbose_name='Criado por')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Tarefa de Projeto Foco'
        verbose_name_plural = 'Tarefas de Projeto Foco'
        ordering = ['prazo', '-criado_em']

    def __str__(self):
        return self.titulo


# ==========================================================================
# INOVAR
# ==========================================================================
class Ideia(models.Model):
    """Ideia submetida por um colaborador (bloco INOVAR)."""

    class Status(models.TextChoices):
        NOVA = 'NOVA', 'Nova'
        EM_ANALISE = 'EM_ANALISE', 'Em análise'
        APROVADA = 'APROVADA', 'Aprovada'
        ARQUIVADA = 'ARQUIVADA', 'Arquivada'

    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='impulso_ideias', verbose_name='Autor')
    descricao = models.TextField(
        verbose_name='Qual é a sua ideia?',
        help_text='Informe de forma concreta a sua ideia.')
    setor_impacto = models.CharField(
        max_length=150, verbose_name='Setor de impacto')
    motivo = models.TextField(verbose_name='Qual o motivo?')

    status = models.CharField(
        max_length=12, choices=Status.choices,
        default=Status.NOVA, verbose_name='Status')
    resposta_gestor = models.TextField(blank=True, verbose_name='Retorno do gestor')

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Ideia'
        verbose_name_plural = 'Ideias'
        ordering = ['-criado_em']

    def __str__(self):
        return f"Ideia de {self.autor} — {self.setor_impacto}"
