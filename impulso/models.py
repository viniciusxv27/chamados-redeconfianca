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
import calendar
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
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


def upload_projeto_anexo(instance, filename):
    return f"impulso/projetos/{instance.projeto_id or 'novos'}/{_uuid_name(filename)}"


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

    class Recorrencia(models.TextChoices):
        """Com que frequência a tarefa volta a ser feita.

        Diferente da antiga "periodicidade", que era só um rótulo: aqui a
        escolha tem efeito real — ao concluir uma meta recorrente, a próxima
        ocorrência é criada automaticamente com o prazo avançado.
        """
        UNICA = 'UNICA', 'Única vez'
        DIARIA = 'DIARIA', 'Diária'
        SEMANAL = 'SEMANAL', 'Semanal'
        QUINZENAL = 'QUINZENAL', 'Quinzenal'
        MENSAL = 'MENSAL', 'Mensal'

    class Aprovacao(models.TextChoices):
        APROVADA = 'APROVADA', 'Aprovada'
        PENDENTE = 'PENDENTE', 'Aguardando aprovação do gestor'
        RECUSADA = 'RECUSADA', 'Recusada'

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
    # Meta compartilhada: além do responsável principal (colaborador), outras
    # pessoas podem tocar a mesma meta. O campo antigo continua sendo o dono —
    # é ele que aparece no Kanban e conta na pontuação individual.
    participantes = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name='impulso_metas_participando', verbose_name='Outros responsáveis')

    titulo = models.CharField(max_length=200, verbose_name='Título da meta')
    descricao = models.TextField(
        verbose_name='Descrição',
        help_text='Descreva a meta de forma clara, por textos.')
    recorrencia = models.CharField(
        max_length=12, choices=Recorrencia.choices,
        default=Recorrencia.UNICA, verbose_name='Recorrência')
    # As metas criadas antes da recorrência existir nasceram com uma
    # "periodicidade" que era apenas informativa. Ligar a geração automática
    # nelas criaria tarefas que ninguém pediu — por isso a migration desliga
    # este campo no acervo antigo e deixa ligado só do lado novo.
    recorrencia_ativa = models.BooleanField(
        default=True, verbose_name='Gerar próxima ocorrência ao concluir')
    recorrencia_de = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ocorrencias', verbose_name='Ocorrência anterior')
    prazo = models.DateField(verbose_name='Prazo')

    # Solicitação feita pelo colaborador, que o gestor precisa aprovar.
    # Metas criadas pelo próprio gestor já nascem APROVADA.
    aprovacao = models.CharField(
        max_length=10, choices=Aprovacao.choices,
        default=Aprovacao.APROVADA, verbose_name='Aprovação')
    solicitada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_metas_solicitadas', verbose_name='Solicitada por')
    decidida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_metas_decididas', verbose_name='Decidida por')
    decidida_em = models.DateTimeField(null=True, blank=True, verbose_name='Decidida em')
    motivo_recusa = models.TextField(blank=True, verbose_name='Motivo da recusa')

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

    # ── Aprovação ──────────────────────────────────────────────────────────
    @property
    def pendente_aprovacao(self):
        return self.aprovacao == self.Aprovacao.PENDENTE

    @property
    def recusada(self):
        return self.aprovacao == self.Aprovacao.RECUSADA

    @property
    def vale_pontos(self):
        """Só meta aprovada entra no Kanban e na pontuação.

        Sem isto, uma solicitação recusada (ou ainda parada na fila do gestor)
        entraria no denominador do CONFIAR e derrubaria a nota de quem apenas
        pediu uma tarefa.
        """
        return self.aprovacao == self.Aprovacao.APROVADA

    def pode_decidir(self, user):
        """Quem aprova/recusa a solicitação.

        O gestor escolhido, o superuser — e qualquer gestor do Impulso da área
        do colaborador. A última parte é o que faz a demanda vinda de outra
        área não ficar parada quando o gestor indicado está de férias ou saiu:
        quem responde pela área pode decidir.

        Quem pediu não decide o próprio pedido, mesmo sendo gestor da área —
        seria aprovar a si mesmo e o "aprovado pelo gestor da área" viraria
        carimbo.
        """
        if not (user and user.is_authenticated) or not self.pendente_aprovacao:
            return False
        if user.is_superuser:
            return True
        if self.solicitada_por_id == user.id:
            return False
        if self.gestor_id == user.id:
            return True
        from .utils import get_gestores_do_setor
        return get_gestores_do_setor(self.colaborador).filter(id=user.id).exists()

    @property
    def responsaveis(self):
        """Quem toca a meta: o dono mais os participantes."""
        pessoas = [self.colaborador]
        pessoas += [u for u in self.participantes.all() if u.id != self.colaborador_id]
        return pessoas

    @property
    def progresso_itens(self):
        """(feitos, total) do to-do interno."""
        itens = list(self.itens.all())
        return sum(1 for i in itens if i.concluido), len(itens)

    def novidades_para(self, user):
        """O que mudou desde a última vez que esta pessoa abriu a meta.

        Sem isso o gestor precisa entrar meta por meta para descobrir se algo
        andou. O card passa a avisar o que tem de novo.
        """
        visto = self.visualizacoes.filter(user=user).first()
        desde = visto.visto_em if visto else None

        comentarios = self.comentarios.exclude(autor=user)
        anexos = self.anexos.exclude(enviado_por=user)
        itens = self.itens.filter(concluido=True).exclude(concluido_por=user)
        if desde:
            comentarios = comentarios.filter(criado_em__gt=desde)
            anexos = anexos.filter(enviado_em__gt=desde)
            itens = itens.filter(concluido_em__gt=desde)

        entregue = bool(self.entregue_em and (not desde or self.entregue_em > desde)
                        and self.status == self.Status.ENTREGUE)
        return {
            'comentarios': comentarios.count(),
            'anexos': anexos.count(),
            'itens': itens.count(),
            'entregue': entregue,
        }

    def pode_excluir(self, user, equipe_ids=None):
        """Quem apaga a meta: gestor, em qualquer card que ele enxerga.

        "Enxergar" é a mesma régua do Kanban: a meta é dele, ele criou, ou o
        colaborador está em algum setor atrelado a ele — principal ou
        secundário. Antes valia só o que ele mesmo criou ou aprovou, e o gestor
        ficava sem poder limpar card de gente da própria área.

        Colaborador nunca apaga — nem a meta dele, nem a que ele mesmo pediu:
        seria uma saída fácil para sumir com tarefa ruim antes da avaliação.
        Solicitação ainda pendente também não se apaga: para essa existe o
        "recusar", que avisa quem pediu em vez de a coisa sumir calada.
        """
        if not (user and user.is_authenticated):
            return False
        if self.pendente_aprovacao:
            return False
        if user.is_superuser:
            return True
        from .utils import (get_colaboradores_do_gestor,     # tardio: utils importa models
                            is_impulso_manager)
        if not is_impulso_manager(user):
            return False
        if (self.created_by_id == user.id or self.decidida_por_id == user.id
                or self.gestor_id == user.id):
            return True
        # `equipe_ids` evita uma consulta por card no Kanban: quem lista muitas
        # metas calcula a equipe uma vez e passa pronto.
        if equipe_ids is not None:
            return self.colaborador_id in equipe_ids
        return get_colaboradores_do_gestor(user).filter(id=self.colaborador_id).exists()

    def pode_editar(self, user, equipe_ids=None):
        """Quem edita a meta.

        A mesma régua do excluir — gestor, em qualquer card que ele enxerga —
        com uma diferença: solicitação ainda pendente **pode** ser editada.
        Corrigir o texto de um pedido antes de aprovar é reversível; apagá-lo
        em silêncio não é.
        """
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser:
            return True
        from .utils import (get_colaboradores_do_gestor,     # tardio: utils importa models
                            is_impulso_manager)
        if not is_impulso_manager(user):
            return False
        if (self.created_by_id == user.id or self.decidida_por_id == user.id
                or self.gestor_id == user.id):
            return True
        if equipe_ids is not None:
            return self.colaborador_id in equipe_ids
        return get_colaboradores_do_gestor(user).filter(id=self.colaborador_id).exists()

    def pode_cancelar_solicitacao(self, user):
        """Quem pede pode desistir — enquanto a solicitação ainda está parada.

        É diferente de excluir uma meta: uma solicitação pendente não está no
        Kanban de ninguém, não vale ponto e não foi avaliada. Sumir com ela não
        esconde nada. Depois de aprovada, a regra volta a ser a do
        `pode_excluir` — aí já é uma tarefa em andamento.
        """
        if not (user and user.is_authenticated):
            return False
        if not self.pendente_aprovacao:
            return False
        return self.solicitada_por_id == user.id

    @property
    def impacto_da_exclusao(self):
        """O que some junto — usado no aviso de confirmação."""
        return {
            'anexos': self.anexos.count(),
            'comentarios': self.comentarios.count(),
            'avaliada': self.is_avaliada,
            'ocorrencias': self.ocorrencias.count(),
        }

    # ── Recorrência ────────────────────────────────────────────────────────
    @property
    def repete(self):
        return (self.recorrencia != self.Recorrencia.UNICA
                and self.recorrencia_ativa and self.vale_pontos)

    def proximo_prazo(self):
        """Prazo da próxima ocorrência, ou None se não repete."""
        if not self.repete or not self.prazo:
            return None
        if self.recorrencia == self.Recorrencia.DIARIA:
            return self.prazo + timedelta(days=1)
        if self.recorrencia == self.Recorrencia.SEMANAL:
            return self.prazo + timedelta(days=7)
        if self.recorrencia == self.Recorrencia.QUINZENAL:
            return self.prazo + timedelta(days=15)
        # Mensal: mesmo dia do mês seguinte, encolhendo quando o mês é curto
        # (31/01 vira 28/02, não 03/03).
        ano = self.prazo.year + (1 if self.prazo.month == 12 else 0)
        mes = 1 if self.prazo.month == 12 else self.prazo.month + 1
        dia = min(self.prazo.day, calendar.monthrange(ano, mes)[1])
        return date(ano, mes, dia)

    def criar_proxima_ocorrencia(self):
        """Gera a próxima ocorrência de uma meta recorrente concluída.

        Idempotente: se esta meta já gerou uma continuação, não gera outra —
        importante porque o gestor pode reabrir e reavaliar a mesma meta.
        """
        proximo = self.proximo_prazo()
        if not proximo or self.ocorrencias.exists():
            return None
        return Meta.objects.create(
            gestor_id=self.gestor_id,
            colaborador_id=self.colaborador_id,
            titulo=self.titulo,
            descricao=self.descricao,
            recorrencia=self.recorrencia,
            recorrencia_ativa=True,
            recorrencia_de=self,
            prazo=proximo,
            aprovacao=self.Aprovacao.APROVADA,
            created_by_id=self.created_by_id,
        )

    @property
    def numero_da_ocorrencia(self):
        """1 para a original, 2 para a primeira repetição, e assim por diante."""
        numero, atual, guarda = 1, self, 0
        while atual.recorrencia_de_id and guarda < 200:
            numero += 1
            atual = atual.recorrencia_de
            guarda += 1
        return numero


class MetaItem(models.Model):
    """Item de to-do dentro de uma meta, com check.

    Serve para quebrar a meta em passos: quem executa marca cada um conforme
    faz, e o gestor acompanha o avanço sem precisar perguntar.
    """

    meta = models.ForeignKey(
        Meta, on_delete=models.CASCADE, related_name='itens', verbose_name='Meta')
    texto = models.CharField(max_length=300, verbose_name='O que fazer')
    ordem = models.PositiveIntegerField(default=0, verbose_name='Ordem')

    concluido = models.BooleanField(default=False, verbose_name='Concluído')
    concluido_em = models.DateTimeField(null=True, blank=True, verbose_name='Concluído em')
    concluido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_itens_concluidos', verbose_name='Marcado por')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_itens_criados', verbose_name='Criado por')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Item da meta'
        verbose_name_plural = 'Itens da meta'
        ordering = ['ordem', 'id']

    def __str__(self):
        return self.texto


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


class MetaVisualizacao(models.Model):
    """Quando cada pessoa viu a meta pela última vez.

    É o marco que define o que é "novo" no card. Uma linha por (meta, usuário).
    """

    meta = models.ForeignKey(
        Meta, on_delete=models.CASCADE, related_name='visualizacoes', verbose_name='Meta')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='impulso_metas_vistas', verbose_name='Usuário')
    visto_em = models.DateTimeField(auto_now=True, verbose_name='Visto em')

    class Meta:
        verbose_name = 'Visualização de meta'
        verbose_name_plural = 'Visualizações de metas'
        constraints = [
            models.UniqueConstraint(fields=['meta', 'user'], name='impulso_meta_vista_unica'),
        ]

    def __str__(self):
        return f'{self.user} viu {self.meta_id} em {self.visto_em:%d/%m/%Y %H:%M}'


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


# Como a nota da IA é apresentada. Classes completas: o Tailwind precisa
# encontrar o nome inteiro no template para gerar o CSS.
FAIXAS_DA_NOTA = {
    'abaixo': {'chave': 'abaixo', 'rotulo': 'Abaixo do esperado', 'icone': 'fa-arrow-down',
               'texto': 'text-red-600', 'texto_forte': 'text-red-700', 'fundo': 'bg-red-50',
               'borda': 'border-red-200', 'barra': 'bg-red-500', 'ponto': 'bg-red-500'},
    'parcial': {'chave': 'parcial', 'rotulo': 'Parcial', 'icone': 'fa-minus',
                'texto': 'text-amber-600', 'texto_forte': 'text-amber-700', 'fundo': 'bg-amber-50',
                'borda': 'border-amber-200', 'barra': 'bg-amber-500', 'ponto': 'bg-amber-500'},
    'esperado': {'chave': 'esperado', 'rotulo': 'Dentro do esperado', 'icone': 'fa-check',
                 'texto': 'text-emerald-600', 'texto_forte': 'text-emerald-700',
                 'fundo': 'bg-emerald-50', 'borda': 'border-emerald-200',
                 'barra': 'bg-emerald-500', 'ponto': 'bg-emerald-500'},
    'acima': {'chave': 'acima', 'rotulo': 'Acima do esperado', 'icone': 'fa-arrow-up',
              'texto': 'text-violet-600', 'texto_forte': 'text-violet-700', 'fundo': 'bg-violet-50',
              'borda': 'border-violet-200', 'barra': 'bg-violet-500', 'ponto': 'bg-violet-500'},
}


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

    # Nota que a IA atribui ao feedback, de 0 a 10. Fica separada dos textos
    # do gestor de propósito: é leitura da IA sobre o que foi escrito, não algo
    # que o gestor digitou. Vazia quando a análise ainda não rodou ou quando a
    # IA respondeu sem uma nota utilizável.
    nota_ia = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('10'))],
        verbose_name='Nota da IA')

    # Resumo IA (padrão reaproveitado de feedback/ai.py)
    ai_summary = models.TextField(blank=True, verbose_name='Resumo IA')
    ai_summary_generated_at = models.DateTimeField(null=True, blank=True)
    ai_summary_error = models.TextField(blank=True)
    ai_tentativas = models.PositiveIntegerField(
        default=0, verbose_name='Tentativas de geração',
        help_text='Quantas chamadas à IA já foram feitas para este feedback.')

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Feedback (Impulso)'
        verbose_name_plural = 'Feedbacks (Impulso)'
        ordering = ['-referencia_mes', '-criado_em']

    def __str__(self):
        return f"Feedback {self.referencia_mes:%m/%Y} — {self.colaborador}"

    @property
    def tem_analise(self):
        return bool(self.ai_summary)

    @property
    def resumo_curto(self):
        """Só o parágrafo do resumo, sem os títulos markdown.

        O card da listagem mostrava "## Pontos a Melhorar" no meio do texto
        quando o corte caía ali. Aqui o texto sai limpo para ser truncado.
        """
        if not self.ai_summary:
            return ''
        linhas = []
        dentro_do_resumo = False
        for linha in self.ai_summary.splitlines():
            crua = linha.strip()
            if crua.startswith('##'):
                # Entra no bloco do resumo e para no título seguinte.
                if dentro_do_resumo:
                    break
                dentro_do_resumo = 'resumo' in crua.lower()
                continue
            if dentro_do_resumo and crua:
                linhas.append(crua)
        # Sem o título esperado, devolve o texto inteiro sem marcação.
        return ' '.join(linhas) or ' '.join(
            l.strip() for l in self.ai_summary.splitlines()
            if l.strip() and not l.strip().startswith('##'))

    @property
    def nota_percentual(self):
        """A nota em 0-100, para barras e anéis na tela."""
        return int(round(float(self.nota_ia) * 10)) if self.nota_ia is not None else None

    @property
    def faixa_da_nota(self):
        """Como ler a nota: abaixo / parcial / esperado / acima.

        As classes vêm escritas por extenso porque o Tailwind não enxerga
        nome montado no template (``bg-{{ cor }}-500`` não vira CSS).
        """
        if self.nota_ia is None:
            return None
        nota = float(self.nota_ia)
        if nota < 5:
            return FAIXAS_DA_NOTA['abaixo']
        if nota < 7:
            return FAIXAS_DA_NOTA['parcial']
        if nota < 9:
            return FAIXAS_DA_NOTA['esperado']
        return FAIXAS_DA_NOTA['acima']


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
        help_text='PDF do POP, material do curso ou o próprio vídeo (opcional).')
    # POP e vídeo deixaram de ser dois cards: o POP escrito e o vídeo que
    # ensina a mesma coisa são um conteúdo só, com dois anexos. Antes disso a
    # pessoa concluía o PDF sem nunca ver o vídeo — e vice-versa.
    video = models.FileField(
        upload_to=upload_conteudo, storage=get_media_storage(),
        null=True, blank=True, verbose_name='Vídeo',
        help_text='Vídeo que acompanha o POP ou o material (opcional).')
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

    # Extensões que o <video> do navegador toca. Um "vídeo" enviado como .avi
    # ou por link externo não dá para controlar, então não vira obrigatório.
    EXTENSOES_VIDEO = ('.mp4', '.webm', '.ogg', '.ogv', '.m4v', '.mov')

    # Vídeo e POP são o mesmo grupo na tela e na pontuação; CURSO é o outro.
    GRUPO_CURSO = 'CURSO'
    GRUPO_POP_VIDEO = 'POP_VIDEO'
    GRUPOS = (
        (GRUPO_CURSO, 'Curso'),
        (GRUPO_POP_VIDEO, 'Vídeo e POP'),
    )
    TIPOS_DO_GRUPO = {
        GRUPO_CURSO: (Tipo.CURSO,),
        GRUPO_POP_VIDEO: (Tipo.VIDEO, Tipo.POP),
    }

    @classmethod
    def grupo_do_tipo(cls, tipo):
        return cls.GRUPO_CURSO if tipo == cls.Tipo.CURSO else cls.GRUPO_POP_VIDEO

    @property
    def grupo(self):
        return self.grupo_do_tipo(self.tipo)

    @staticmethod
    def _e_video(campo):
        if not campo:
            return False
        return (campo.name or '').lower().endswith(
            ConteudoConectar.EXTENSOES_VIDEO)

    @property
    def arquivo_de_video(self):
        """O vídeo que o portal consegue acompanhar do início ao fim.

        Olha o campo `video` primeiro e cai no `arquivo` depois: os vídeos
        subidos antes do campo existir moram no `arquivo`, e continuar tocando
        esses é o que impede a mudança de zerar o histórico de quem já assistiu.
        """
        if self._e_video(self.video):
            return self.video
        if self._e_video(self.arquivo):
            return self.arquivo
        return None

    @property
    def documento(self):
        """O anexo que não é vídeo — o PDF do POP, o material do curso."""
        if self.arquivo and not self._e_video(self.arquivo):
            return self.arquivo
        return None

    @property
    def video_reproduzivel(self):
        """True quando há um vídeo hospedado que o portal consegue acompanhar."""
        return self.arquivo_de_video is not None

    @property
    def rotulo(self):
        """O que este conteúdo é, pelo que ele carrega — não pelo `tipo`.

        Um item do grupo Vídeo e POP pode ter os dois anexos; dizer só "POP"
        esconderia o vídeo de quem precisa assistir.
        """
        if self.tipo == self.Tipo.CURSO:
            return 'Curso'
        tem_video = self.arquivo_de_video is not None
        tem_doc = self.documento is not None
        if tem_video and tem_doc:
            return 'Vídeo e POP'
        return 'Vídeo' if tem_video else 'POP'

    def periodo_ativo(self):
        hoje = timezone.localdate()
        if self.inicio and hoje < self.inicio:
            return False
        if self.fim and hoje > self.fim:
            return False
        return True

    def pode_excluir(self, user):
        """Quem apaga o conteúdo: SUPERADMIN ou gestor do Impulso.

        Vale para qualquer conteúdo, não só o que a pessoa subiu — quem cuida
        do módulo precisa poder tirar do ar material errado ou vencido, mesmo
        que outro gestor tenha publicado.
        """
        if not (user and getattr(user, 'is_authenticated', False)):
            return False
        if user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN':
            return True
        from .utils import is_impulso_manager       # tardio: utils importa models
        return is_impulso_manager(user)

    @property
    def impacto_da_exclusao(self):
        """O que some junto. A tela avisa antes de perguntar 'tem certeza?'.

        Apagar o conteúdo leva as conclusões embora, e conclusão é ponto do
        mês de quem fez. Quem apaga precisa ver o tamanho disso antes.
        """
        conclusoes = self.conclusoes.all()
        return {
            'conclusoes': conclusoes.count(),
            'concluidos': conclusoes.filter(concluido=True).count(),
            'certificados': conclusoes.exclude(certificado='').count(),
            'dirigido_a': self.obrigatorio_para.count(),
        }


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

    # ── Aprovação, no mesmo desenho do Confiar ──────────────────────────────
    # "Marquei como feito" não é a mesma coisa que "está feito": o certificado
    # pode estar ilegível, ser de outro curso ou o POP não ter sido aplicado.
    # Sem alguém conferir, os pontos do Conectar valiam pela palavra de quem
    # marcou — e não havia como saber, na tela, o que já foi checado.
    class Aprovacao(models.TextChoices):
        PENDENTE = 'PENDENTE', 'Aguardando conferência'
        APROVADA = 'APROVADA', 'Aprovada'
        RECUSADA = 'RECUSADA', 'Recusada'

    aprovacao = models.CharField(
        max_length=10, choices=Aprovacao.choices, default=Aprovacao.PENDENTE,
        db_index=True, verbose_name='Aprovação')
    decidida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_conclusoes_decididas', verbose_name='Conferido por')
    decidida_em = models.DateTimeField(null=True, blank=True, verbose_name='Conferido em')
    observacao = models.TextField(
        blank=True, verbose_name='Observação de quem conferiu',
        help_text='Obrigatória na recusa: é o que a pessoa lê para corrigir.')

    # Acompanhamento do vídeo. Guardado no servidor porque a checagem no
    # navegador sozinha não vale nada: bastaria um POST direto para "concluir"
    # sem ter assistido.
    video_assistido_ate = models.FloatField(
        default=0, verbose_name='Assistido até (segundos)',
        help_text='Maior ponto do vídeo alcançado sem pular à frente.')
    video_duracao = models.FloatField(default=0, verbose_name='Duração do vídeo (segundos)')
    video_concluido = models.BooleanField(default=False, verbose_name='Vídeo assistido até o fim')
    video_atualizado_em = models.DateTimeField(
        null=True, blank=True, verbose_name='Último aviso de progresso')

    # Considera assistido a partir daqui: os segundos finais costumam ser
    # créditos/encerramento, e exigir 100% trava quem tem vídeo com fade out.
    FRACAO_PARA_CONCLUIR = 0.95

    class Meta:
        verbose_name = 'Conclusão de Conteúdo'
        verbose_name_plural = 'Conclusões de Conteúdo'
        unique_together = ('conteudo', 'user')
        ordering = ['-concluido_em']

    def __str__(self):
        return f"{self.user} — {self.conteudo} ({'ok' if self.concluido else 'pendente'})"

    @property
    def aprovada(self):
        return self.aprovacao == self.Aprovacao.APROVADA

    @property
    def aguardando(self):
        """Marcada como feita e ainda não conferida."""
        return self.concluido and self.aprovacao == self.Aprovacao.PENDENTE

    @property
    def vale_ponto(self):
        """O que de fato conta na pontuação do Conectar.

        Só o que foi conferido. Antes bastava a pessoa marcar; agora a régua é
        a mesma do Confiar, onde a entrega passa pelo gestor.
        """
        return self.concluido and self.aprovacao == self.Aprovacao.APROVADA

    def pode_decidir(self, user):
        """Quem aprova ou recusa esta conclusão.

        Gestor do Impulso, e nunca a própria pessoa — aprovar o próprio
        certificado transformaria a conferência em carimbo. É a mesma regra do
        `Meta.pode_decidir`, sem a parte de "quem pediu", que aqui não existe.
        """
        if not (user and user.is_authenticated) or not self.concluido:
            return False
        if user.is_superuser:
            return True
        if self.user_id == user.id:
            return False
        from .utils import is_impulso_manager
        return is_impulso_manager(user)


class ProjetoFoco(models.Model):
    """Projeto foco: o gestor adequa a equipe e as atribuições (tarefas)."""

    nome = models.CharField(max_length=200, verbose_name='Nome do projeto')
    descricao = models.TextField(blank=True, verbose_name='Descrição')
    membros = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name='impulso_projetos_foco', verbose_name='Equipe do projeto')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')

    # Metade dos 20 pontos do Projeto FOCO depende de o projeto ter saído, não
    # só de cada um ter feito a sua parte. Por isso a conclusão é um estado do
    # projeto, marcado por quem responde por ele — e não algo deduzido de
    # "todas as tarefas estão concluídas": projeto sem tarefa nenhuma passaria
    # por concluído, e a última tarefa marcada por engano encerraria o projeto.
    concluido = models.BooleanField(default=False, verbose_name='Projeto concluído')
    concluido_em = models.DateTimeField(null=True, blank=True,
                                        verbose_name='Concluído em')
    concluido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_projetos_concluidos', verbose_name='Concluído por')

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

    @property
    def progresso_tarefas(self):
        """(concluídas, total) das tarefas do projeto."""
        tarefas = list(self.tarefas.all())
        feitas = sum(1 for t in tarefas
                     if t.status == TarefaProjeto.Status.CONCLUIDA)
        return feitas, len(tarefas)

    @property
    def tudo_entregue(self):
        """Todas as tarefas concluídas — o momento de encerrar o projeto."""
        feitas, total = self.progresso_tarefas
        return bool(total) and feitas == total


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


class ProjetoAnexo(models.Model):
    """Anexo de um projeto foco: arquivo enviado OU link externo.

    Mesmo desenho do anexo de meta — projeto foco também acumula planilha,
    apresentação e link de referência, e sem isso o material ficava espalhado
    em conversa de WhatsApp.
    """

    class Tipo(models.TextChoices):
        ARQUIVO = 'ARQUIVO', 'Arquivo'
        LINK = 'LINK', 'Link'

    projeto = models.ForeignKey(
        ProjetoFoco, on_delete=models.CASCADE, related_name='anexos',
        verbose_name='Projeto')
    tipo = models.CharField(max_length=8, choices=Tipo.choices, verbose_name='Tipo')
    titulo = models.CharField(max_length=200, blank=True, verbose_name='Título')
    arquivo = models.FileField(
        upload_to=upload_projeto_anexo, storage=get_media_storage(),
        null=True, blank=True, verbose_name='Arquivo')
    url = models.URLField(blank=True, verbose_name='URL')

    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='impulso_projeto_anexos', verbose_name='Enviado por')
    enviado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Anexo de Projeto Foco'
        verbose_name_plural = 'Anexos de Projetos Foco'
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

    def pode_mexer(self, user):
        """Quem edita ou apaga.

        Quem anexou mexe no que é seu; quem criou o projeto e o superusuário
        mexem em qualquer um, porque respondem pelo projeto. Membro não apaga
        anexo de colega — remover arquivo não tem volta.
        """
        if not (user and getattr(user, 'is_authenticated', False)):
            return False
        return (user.is_superuser
                or self.enviado_por_id == user.id
                or self.projeto.criado_por_id == user.id)


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

    # Quem mais assina a ideia. Pontua junto com o autor — ideia boa raramente
    # nasce de uma cabeça só, e sem isso quem ajudou fica de fora da nota.
    participantes = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name='impulso_ideias_participando',
        verbose_name='Também participaram',
        help_text='Até 3 pessoas, além do autor.')

    status = models.CharField(
        max_length=12, choices=Status.choices,
        default=Status.NOVA, verbose_name='Status')
    resposta_gestor = models.TextField(blank=True, verbose_name='Retorno do gestor')

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    MAX_PARTICIPANTES = 3

    class Meta:
        verbose_name = 'Ideia'
        verbose_name_plural = 'Ideias'
        ordering = ['-criado_em']

    def __str__(self):
        return f"Ideia de {self.autor} — {self.setor_impacto}"

    @property
    def editavel(self):
        """O autor só edita enquanto o gestor não decidiu.

        Depois de aprovada ou arquivada, o texto fica travado — senão a decisão
        do gestor passaria a valer para uma ideia diferente da que ele leu.
        """
        return self.status in (self.Status.NOVA, self.Status.EM_ANALISE)

    def pode_editar(self, user):
        if not user or not user.is_authenticated:
            return False
        if not self.editavel:
            return False
        return self.autor_id == user.id or user.is_superuser


# ==========================================================================
# ACOMPANHAMENTO — Ciclos, meses e pontuação
# ==========================================================================
class Faixa(models.TextChoices):
    """Faixas/medalhas. Ordem: Impulso (100%) > Ouro > Prata > Bronze."""
    IMPULSO = 'IMPULSO', 'Impulso'
    OURO = 'OURO', 'Ouro'
    PRATA = 'PRATA', 'Prata'
    BRONZE = 'BRONZE', 'Bronze'


class Ciclo(models.Model):
    """Ciclo de avaliação do Impulso (normalmente 3 meses).

    Os pontos "reiniciam" a cada mês: cada CicloMes guarda a pontuação daquele
    mês. No fim do ciclo é possível ver a nota total e a sequência de medalhas,
    e as confianças acumuladas (Ouro/Impulso) são creditadas.
    """

    class Status(models.TextChoices):
        ABERTO = 'ABERTO', 'Aberto'
        ENCERRADO = 'ENCERRADO', 'Encerrado'

    nome = models.CharField(max_length=120, verbose_name='Nome do ciclo')
    inicio = models.DateField(verbose_name='Início')
    fim = models.DateField(verbose_name='Fim')
    status = models.CharField(
        max_length=10, choices=Status.choices,
        default=Status.ABERTO, verbose_name='Status')

    encerrado_em = models.DateTimeField(null=True, blank=True, verbose_name='Encerrado em')
    encerrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_ciclos_encerrados', verbose_name='Encerrado por')
    # Trava de idempotência: garante que as confianças do ciclo só são pagas 1x.
    confiancas_creditadas = models.BooleanField(
        default=False, verbose_name='Confianças já creditadas')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='impulso_ciclos_criados', verbose_name='Criado por')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Ciclo'
        verbose_name_plural = 'Ciclos'
        ordering = ['-inicio']

    def __str__(self):
        return self.nome

    @property
    def is_aberto(self):
        return self.status == self.Status.ABERTO


class CicloMes(models.Model):
    """Um mês dentro do ciclo. Ao ser fechado, congela a pontuação de todos."""

    class Status(models.TextChoices):
        ABERTO = 'ABERTO', 'Aberto'
        FECHADO = 'FECHADO', 'Fechado'

    ciclo = models.ForeignKey(
        Ciclo, on_delete=models.CASCADE, related_name='meses', verbose_name='Ciclo')
    referencia = models.DateField(
        verbose_name='Mês de referência',
        help_text='Primeiro dia do mês de referência.')
    status = models.CharField(
        max_length=10, choices=Status.choices,
        default=Status.ABERTO, verbose_name='Status')

    fechado_em = models.DateTimeField(null=True, blank=True, verbose_name='Fechado em')
    fechado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_meses_fechados', verbose_name='Fechado por')

    class Meta:
        verbose_name = 'Mês do Ciclo'
        verbose_name_plural = 'Meses do Ciclo'
        ordering = ['referencia']
        unique_together = ('ciclo', 'referencia')

    def __str__(self):
        return f"{self.referencia:%m/%Y} — {self.ciclo.nome}"

    @property
    def is_fechado(self):
        return self.status == self.Status.FECHADO


class PontuacaoMensal(models.Model):
    """Snapshot da pontuação de um colaborador em um mês (total 100 pontos).

    CONFIAR 40 = metas 20 (10 qualidade + 10 conclusão) + feedback 10 + assiduidade 10
    CONECTAR 40 = curso 10 + vídeos/POPs 10 + projeto foco 20
    INOVAR 20 = 3 ideias 10 + 1 ideia aprovada 10
    """

    mes = models.ForeignKey(
        CicloMes, on_delete=models.CASCADE, related_name='pontuacoes', verbose_name='Mês')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='impulso_pontuacoes', verbose_name='Colaborador')
    setor = models.ForeignKey(
        'users.Sector', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='impulso_pontuacoes', verbose_name='Setor principal',
        help_text='Setor do colaborador no momento do fechamento (para o Setor Destaque).')

    # CONFIAR (40)
    p_metas_qualidade = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                            verbose_name='Metas — qualidade (0-10)')
    p_metas_conclusao = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                            verbose_name='Metas — conclusão (0-10)')
    p_feedback = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                     verbose_name='Feedback (0-10)')
    p_assiduidade = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                        verbose_name='Assiduidade (0-10)')
    # CONECTAR (40)
    p_curso = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                  verbose_name='Curso do mês (0-10)')
    p_videos_pops = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                        verbose_name='Vídeos e POPs (0-10)')
    p_projeto_foco = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                         verbose_name='Projeto foco (0-20)')
    # INOVAR (20)
    p_ideias = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                   verbose_name='Ideias propostas (0-10)')
    p_ideia_aprovada = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                           verbose_name='Ideia aprovada (0-10)')

    total = models.DecimalField(max_digits=6, decimal_places=2, default=0,
                                verbose_name='Total (0-100)')
    pontos_aplicaveis = models.DecimalField(
        max_digits=6, decimal_places=2, default=100,
        verbose_name='Pontos aplicáveis',
        help_text='Máximo possível no mês (itens sem nada configurado não contam).')
    percentual = models.DecimalField(max_digits=6, decimal_places=2, default=0,
                                     verbose_name='Percentual (%)')
    faixa = models.CharField(max_length=10, choices=Faixa.choices,
                             default=Faixa.BRONZE, verbose_name='Faixa')
    confiancas_previstas = models.PositiveIntegerField(
        default=0, verbose_name='Confianças previstas',
        help_text='Creditadas ao encerrar o ciclo (Ouro e Impulso).')
    # Métricas cruas usadas no cálculo, para a tela de detalhamento.
    detalhes = models.JSONField(default=dict, blank=True, verbose_name='Detalhes do cálculo')

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Pontuação Mensal'
        verbose_name_plural = 'Pontuações Mensais'
        ordering = ['-total']
        unique_together = ('mes', 'user')

    def __str__(self):
        return f"{self.user} — {self.mes.referencia:%m/%Y}: {self.total}"

    @property
    def total_confiar(self):
        return (self.p_metas_qualidade + self.p_metas_conclusao
                + self.p_feedback + self.p_assiduidade)

    @property
    def total_conectar(self):
        return self.p_curso + self.p_videos_pops + self.p_projeto_foco

    @property
    def total_inovar(self):
        return self.p_ideias + self.p_ideia_aprovada


class ExcecaoAssiduidade(models.Model):
    """Um dia em que o ajuste de ponto não conta contra a assiduidade.

    Serve para o dia em que a culpa não foi de ninguém — relógio fora do ar,
    sistema do Tangerino travado, loja sem energia. Todo mundo teve de ajustar
    o ponto daquele dia, e sem isso o mês inteiro perderia os 10 pontos por
    algo que a pessoa não causou.

    O alcance é estreito de propósito: o dia continua sendo dia útil, continua
    precisando das 4 batidas e a falta injustificada continua zerando. A
    exceção **só** tira o ajuste daquele dia da conta dos 3 do mês.

    Quem cria é o SUPERADMIN, e o motivo é obrigatório: daqui a três meses
    ninguém lembra por que 12/03 estava liberado.
    """

    data = models.DateField(unique=True, verbose_name='Dia')
    motivo = models.CharField(
        max_length=200, verbose_name='Motivo',
        help_text='Aparece na tela para todo mundo. Ex.: "relógio de ponto fora do ar".')
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Criado por')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Exceção de assiduidade'
        verbose_name_plural = 'Exceções de assiduidade'
        ordering = ['-data']

    def __str__(self):
        return f'{self.data:%d/%m/%Y} — {self.motivo}'

    @classmethod
    def dias_do_mes(cls, ano, mes):
        """Os dias liberados naquele mês, como set de date."""
        return set(cls.objects.filter(data__year=ano, data__month=mes)
                   .values_list('data', flat=True))


class PesosImpulso(models.Model):
    """Quantos pontos vale cada tarefa dos 3 pilares.

    Os pesos eram constantes no código: mudar a régua exigia deploy. Aqui o
    SUPERADMIN redistribui os 100 pontos e desliga a tarefa que não vale para
    o ciclo — uma rede sem Projeto FOCO num mês, por exemplo, não pode carregar
    20 pontos impossíveis.

    São dois níveis, na mesma tabela:

    * **régua geral** (``usuario`` vazio) — vale para todo mundo. É a linha que
      já existia, e enquanto ninguém mexer os valores são exatamente os que o
      código usava.
    * **régua de uma pessoa** (``usuario`` preenchido) — quem tem função que
      não encosta em Projeto FOCO, por exemplo, não pode carregar 20 pontos
      impossíveis só porque a régua geral os tem. Existe só para quem foi
      ajustado; os demais continuam na geral, sem cópia nenhuma no banco.
    """

    # (campo, rótulo, pilar, valor de origem)
    ITENS = (
        ('metas_qualidade', 'Qualidade das metas', 'CONFIAR', 10),
        ('metas_conclusao', 'Conclusão das metas', 'CONFIAR', 10),
        ('feedback', 'Feedback do gestor', 'CONFIAR', 10),
        ('assiduidade', 'Assiduidade', 'CONFIAR', 10),
        ('curso', 'Curso', 'CONECTAR', 10),
        ('videos_pops', 'Vídeos e POPs', 'CONECTAR', 10),
        # Metade sai pela entrega da parte de cada um, metade pela conclusão
        # do projeto. O peso configurado aqui é o total dos dois.
        ('projeto_foco', 'Projeto FOCO (metade entrega, metade conclusão)',
         'CONECTAR', 20),
        ('ideias', 'Ideias propostas', 'INOVAR', 10),
        ('ideia_aprovada', 'Ideia aprovada', 'INOVAR', 10),
    )
    PILARES = (('CONFIAR', 'Confiar'), ('CONECTAR', 'Conectar'), ('INOVAR', 'Inovar'))
    TOTAL_ESPERADO = Decimal('100')

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name='pesos_impulso', verbose_name='Pessoa',
        help_text='Vazio = régua geral, que vale para quem não tem a própria.')

    metas_qualidade = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    metas_conclusao = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    feedback = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    assiduidade = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    curso = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    videos_pops = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    projeto_foco = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    ideias = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    ideia_aprovada = models.DecimalField(max_digits=5, decimal_places=2, default=10)

    # Tarefa desligada vale zero e some da tela de acompanhamento. Guardar o
    # peso dela em vez de zerar permite religar sem redigitar.
    ativos = models.JSONField(default=dict, blank=True, verbose_name='Tarefas ativas')

    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+')

    class Meta:
        verbose_name = 'Pesos do Impulso'
        verbose_name_plural = 'Pesos do Impulso'
        # O OneToOne já garante uma régua por pessoa. A régua geral (usuario
        # nulo) não dá para travar por constraint — no Postgres vários NULL
        # convivem no mesmo índice único —, então quem garante uma só é o
        # get(), que sempre devolve a primeira.

    def __str__(self):
        if self.usuario_id:
            return f'Pesos do Impulso — {self.usuario} ({self.total} pontos)'
        return f'Pesos do Impulso — geral ({self.total} pontos)'

    @property
    def e_geral(self):
        return self.usuario_id is None

    @classmethod
    def get(cls):
        """A régua geral. Continua sendo o padrão de todo mundo."""
        obj = cls.objects.filter(usuario__isnull=True).order_by('pk').first()
        if obj is None:
            obj = cls.objects.create(usuario=None)
        return obj

    @classmethod
    def para(cls, user):
        """A régua que vale para esta pessoa: a dela, ou a geral."""
        if user is not None and getattr(user, 'pk', None):
            propria = cls.objects.filter(usuario_id=user.pk).first()
            if propria is not None:
                return propria
        return cls.get()

    @classmethod
    def propria_de(cls, user):
        """A régua exclusiva desta pessoa, ou None se ela usa a geral."""
        if user is None or not getattr(user, 'pk', None):
            return None
        return cls.objects.filter(usuario_id=user.pk).first()

    def esta_ativo(self, campo):
        """Tarefa desligada explicitamente? Sem registro, está ligada."""
        return bool((self.ativos or {}).get(campo, True))

    def peso(self, campo):
        """Pontos que a tarefa vale agora. Desligada vale zero."""
        if not self.esta_ativo(campo):
            return Decimal('0')
        return Decimal(getattr(self, campo, 0) or 0)

    @property
    def total(self):
        return sum((self.peso(c) for c, _, _, _ in self.ITENS), Decimal('0'))

    @property
    def fecha_em_100(self):
        return self.total == self.TOTAL_ESPERADO

    def por_pilar(self):
        """[(pilar, rótulo, [itens], total)] — é como a tela desenha."""
        rotulos = dict(self.PILARES)
        saida = []
        for pilar in rotulos:
            itens = [
                {'campo': c, 'rotulo': r, 'valor': Decimal(getattr(self, c) or 0),
                 'ativo': self.esta_ativo(c), 'peso': self.peso(c)}
                for c, r, p, _ in self.ITENS if p == pilar
            ]
            saida.append({
                'pilar': pilar, 'rotulo': rotulos[pilar], 'itens': itens,
                'total': sum((i['peso'] for i in itens), Decimal('0')),
            })
        return saida
