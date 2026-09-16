"""Assistente de Apresentações: templates, apresentações, mídias e tarefas da IA.

O conteúdo de uma apresentação é um documento JSON (`Apresentacao.documento`)
num quadro fixo de 1920×1080 — o formato está em `apresentacoes/formato.py`.
Guardar o deck inteiro num campo só é o que deixa o editor salvar de uma vez,
voltar versões e exportar sem remontar dezenas de linhas de tabela.
"""
import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.storage import get_media_storage


def upload_midia(instance, filename):
    # Nome aleatório: o nome original fica em `Midia.nome`, e o caminho no storage não revela nada.
    ext = os.path.splitext(filename or '')[1].lower()[:10]
    hoje = timezone.localdate()
    return f'apresentacoes/midias/{hoje:%Y/%m}/{uuid.uuid4().hex}{ext}'


class ConfiguracaoApresentacoes(models.Model):
    """Configuração única do módulo (pk=1): quem usa e quais modelos de IA."""

    liberados = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='apresentacoes_liberadas',
        verbose_name='Quem pode usar',
        help_text='Além do SUPERADMIN, que sempre pode.')

    modelo_texto = models.CharField(max_length=60, default='gpt-4.1', verbose_name='Modelo de texto e visão')
    modelo_imagem = models.CharField(max_length=60, default='gpt-image-1', verbose_name='Modelo de imagem')
    modelo_video = models.CharField(max_length=60, default='sora-2', verbose_name='Modelo de vídeo')
    modelo_voz = models.CharField(max_length=60, default='gpt-4o-mini-tts', verbose_name='Modelo de voz')
    voz = models.CharField(max_length=30, default='nova', verbose_name='Voz da narração')

    # Canva Connect: o segredo fica cifrado e nunca volta para a tela.
    canva_client_id = models.CharField(max_length=120, blank=True, default='', verbose_name='Canva — Client ID')
    canva_client_secret_cifrado = models.TextField(blank=True, default='', verbose_name='Canva — Client secret (cifrado)')

    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Atualizado por')

    class Meta:
        verbose_name = 'Configuração das apresentações'
        verbose_name_plural = 'Configuração das apresentações'

    def __str__(self):
        return 'Configuração do Assistente de Apresentações'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def canva_configurado(self):
        return bool(self.canva_client_id and self.canva_client_secret_cifrado)

    def set_canva_secret(self, bruto):
        from assistente.crypto import cifrar
        self.canva_client_secret_cifrado = cifrar(bruto) if bruto else ''

    def get_canva_secret(self):
        if not self.canva_client_secret_cifrado:
            return ''
        from assistente.crypto import decifrar
        try:
            return decifrar(self.canva_client_secret_cifrado)
        except Exception:                                       # noqa: BLE001 — SECRET_KEY trocada
            return ''


class TemplateApresentacao(models.Model):
    """Identidade visual: tema (cores e fontes) e layouts (capa, conteúdo, encerramento…).

    Os layouts são guardados no mesmo formato de documento das apresentações:
    cada "slide" do template é um layout, com o fundo e os elementos que viram
    lugar de título, texto, paginação etc. (campo `slot`). Assim o mesmo editor
    edita os dois.
    """

    class Origem(models.TextChoices):
        SISTEMA = 'SISTEMA', 'Padrão do portal'
        IMAGENS = 'IMAGENS', 'Importado de imagens'
        PPTX = 'PPTX', 'Importado de PowerPoint'
        PDF = 'PDF', 'Importado de PDF'

    class Status(models.TextChoices):
        PRONTO = 'PRONTO', 'Pronto'
        ANALISANDO = 'ANALISANDO', 'Analisando'
        ERRO = 'ERRO', 'Erro na análise'

    nome = models.CharField(max_length=120, verbose_name='Nome')
    descricao = models.TextField(blank=True, default='', verbose_name='Descrição')
    estilo = models.TextField(
        blank=True, default='', verbose_name='Estilo para a IA',
        help_text='Como é a identidade: tom, cores, tipo de imagem. A IA segue isso ao montar os slides.')
    documento = models.JSONField(default=dict, blank=True, verbose_name='Tema e layouts')
    origem = models.CharField(max_length=10, choices=Origem.choices, default=Origem.IMAGENS)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PRONTO)
    erro = models.TextField(blank=True, default='')
    padrao_da_rede = models.BooleanField(
        default=False, verbose_name='Disponível para todos',
        help_text='Aparece para todo mundo que usa o módulo. Só o SUPERADMIN marca.')
    principal = models.BooleanField(default=False, verbose_name='Template principal (vem marcado)')
    ativo = models.BooleanField(default=True)
    revisao = models.PositiveIntegerField(default=0)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='apresentacoes_templates', verbose_name='Criado por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Template de apresentação'
        verbose_name_plural = 'Templates de apresentação'
        ordering = ['-principal', '-padrao_da_rede', 'nome']

    def __str__(self):
        return self.nome

    @property
    def layouts(self):
        return (self.documento or {}).get('slides') or []

    @property
    def tema(self):
        return (self.documento or {}).get('tema') or {}


class Apresentacao(models.Model):
    class Status(models.TextChoices):
        RASCUNHO = 'RASCUNHO', 'Rascunho'
        GERANDO = 'GERANDO', 'Gerando com IA'
        PERGUNTAS = 'PERGUNTAS', 'Aguardando respostas'
        PRONTA = 'PRONTA', 'Pronta'
        ERRO = 'ERRO', 'Erro na geração'

    class Origem(models.TextChoices):
        MANUAL = 'MANUAL', 'Pedido livre'
        MODULO = 'MODULO', 'Módulo do portal'

    titulo = models.CharField(max_length=200, default='Nova apresentação', verbose_name='Título')
    pedido = models.TextField(blank=True, default='', verbose_name='Pedido para a IA')
    dono = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='apresentacoes',
        verbose_name='Criada por')
    template = models.ForeignKey(
        TemplateApresentacao, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='apresentacoes', verbose_name='Template')
    documento = models.JSONField(default=dict, blank=True, verbose_name='Slides')
    revisao = models.PositiveIntegerField(default=0, verbose_name='Revisão')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RASCUNHO, db_index=True)
    origem = models.CharField(max_length=8, choices=Origem.choices, default=Origem.MANUAL, db_index=True)
    modulo = models.CharField(max_length=60, blank=True, default='', db_index=True,
                              verbose_name='Módulo apresentado')
    opcoes = models.JSONField(default=dict, blank=True, verbose_name='Opções do pedido')
    erro = models.TextField(blank=True, default='')
    canva_design_id = models.CharField(max_length=80, blank=True, default='')
    canva_edit_url = models.URLField(max_length=1000, blank=True, default='')

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Apresentação'
        verbose_name_plural = 'Apresentações'
        ordering = ['-atualizado_em']

    def __str__(self):
        return self.titulo

    @property
    def slides(self):
        return (self.documento or {}).get('slides') or []

    @property
    def total_slides(self):
        return len(self.slides)


class Midia(models.Model):
    """Arquivo usado nos slides: upload, print, captura de tela ou gerado pela IA.

    Servido pelo portal (`/apresentacoes/midia/<id>/`) e não direto do storage:
    mesma origem é o que deixa o navegador desenhar a imagem no PDF sem CORS, e
    a permissão é conferida a cada pedido.
    """

    class Tipo(models.TextChoices):
        IMAGEM = 'IMAGEM', 'Imagem'
        VIDEO = 'VIDEO', 'Vídeo'
        AUDIO = 'AUDIO', 'Áudio'
        DOCUMENTO = 'DOCUMENTO', 'Documento'

    class Origem(models.TextChoices):
        UPLOAD = 'UPLOAD', 'Enviada'
        PRINT = 'PRINT', 'Print para a IA'
        CAPTURA = 'CAPTURA', 'Captura de tela do portal'
        IA_IMAGEM = 'IA_IMAGEM', 'Imagem gerada pela IA'
        IA_VIDEO = 'IA_VIDEO', 'Vídeo gerado pela IA'
        IA_VOZ = 'IA_VOZ', 'Narração gerada pela IA'
        TEMPLATE = 'TEMPLATE', 'Do template'
        EXPORTACAO = 'EXPORTACAO', 'Exportação'

    dono = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='apresentacoes_midias', verbose_name='Enviada por')
    apresentacao = models.ForeignKey(
        Apresentacao, on_delete=models.CASCADE, null=True, blank=True, related_name='midias')
    template = models.ForeignKey(
        TemplateApresentacao, on_delete=models.CASCADE, null=True, blank=True, related_name='midias')
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.IMAGEM, db_index=True)
    origem = models.CharField(max_length=10, choices=Origem.choices, default=Origem.UPLOAD)
    arquivo = models.FileField(upload_to=upload_midia, storage=get_media_storage(), max_length=300)
    nome = models.CharField(max_length=200, blank=True, default='')
    mime = models.CharField(max_length=100, blank=True, default='')
    tamanho = models.PositiveBigIntegerField(default=0)
    largura = models.PositiveIntegerField(null=True, blank=True)
    altura = models.PositiveIntegerField(null=True, blank=True)
    duracao = models.FloatField(null=True, blank=True, verbose_name='Duração (s)')
    prompt = models.TextField(blank=True, default='', verbose_name='Pedido à IA')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Mídia de apresentação'
        verbose_name_plural = 'Mídias de apresentação'
        ordering = ['-criado_em']

    def __str__(self):
        return self.nome or os.path.basename(self.arquivo.name or '') or f'Mídia {self.pk}'

    @property
    def url(self):
        from django.urls import reverse
        return reverse('apresentacoes:midia', args=[self.pk])

    def como_json(self):
        return {
            'id': self.pk, 'url': self.url, 'tipo': self.tipo, 'origem': self.origem, 'nome': self.nome,
            'largura': self.largura, 'altura': self.altura, 'duracao': self.duracao, 'mime': self.mime,
        }


class TarefaIA(models.Model):
    """Trabalho da IA que roda em segundo plano (a tela acompanha pelo status).

    A geração leva de segundos a minutos. Segurar a requisição prenderia um
    worker do gunicorn; aqui a tarefa roda numa thread e a tela pergunta o
    andamento. Pergunta da IA ao usuário vira status PERGUNTAS: a resposta
    chega por outra requisição e a tarefa continua de onde parou.
    """

    class Tipo(models.TextChoices):
        GERAR = 'GERAR', 'Gerar apresentação'
        EDITAR = 'EDITAR', 'Editar apresentação com IA'
        REFAZER_SLIDE = 'REFAZER_SLIDE', 'Refazer slide'
        NOVO_SLIDE = 'NOVO_SLIDE', 'Novo slide'
        TEXTO = 'TEXTO', 'Reescrever texto'
        IMAGEM = 'IMAGEM', 'Gerar imagem'
        VIDEO = 'VIDEO', 'Gerar vídeo'
        NARRACAO = 'NARRACAO', 'Gerar narração'
        ANALISAR_TEMPLATE = 'ANALISAR_TEMPLATE', 'Analisar template'
        CANVA = 'CANVA', 'Enviar ao Canva'

    class Status(models.TextChoices):
        PENDENTE = 'PENDENTE', 'Na fila'
        RODANDO = 'RODANDO', 'Rodando'
        PERGUNTAS = 'PERGUNTAS', 'Aguardando respostas'
        CONCLUIDA = 'CONCLUIDA', 'Concluída'
        ERRO = 'ERRO', 'Erro'

    tipo = models.CharField(max_length=20, choices=Tipo.choices, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='apresentacoes_tarefas')
    apresentacao = models.ForeignKey(
        Apresentacao, on_delete=models.CASCADE, null=True, blank=True, related_name='tarefas')
    template = models.ForeignKey(
        TemplateApresentacao, on_delete=models.CASCADE, null=True, blank=True, related_name='tarefas')
    parametros = models.JSONField(default=dict, blank=True)
    progresso = models.JSONField(default=dict, blank=True)
    perguntas = models.JSONField(default=list, blank=True)
    respostas = models.JSONField(default=dict, blank=True)
    resultado = models.JSONField(default=dict, blank=True)
    erro = models.TextField(blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    concluida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Tarefa da IA'
        verbose_name_plural = 'Tarefas da IA'
        ordering = ['-criado_em']

    def __str__(self):
        return f'{self.get_tipo_display()} #{self.pk} ({self.get_status_display()})'

    @property
    def em_andamento(self):
        return self.status in (self.Status.PENDENTE, self.Status.RODANDO, self.Status.PERGUNTAS)

    def como_json(self):
        return {
            'id': self.pk, 'tipo': self.tipo, 'status': self.status, 'progresso': self.progresso or {},
            'erro': self.erro, 'perguntas': self.perguntas or [], 'resultado': self.resultado or {},
            'apresentacao': self.apresentacao_id, 'template': self.template_id,
            # Último sinal de vida: a tela avisa "está demorando" em vez de só girar.
            'atualizado_em': self.atualizado_em.isoformat() if self.atualizado_em else None,
        }


class MensagemIA(models.Model):
    """O diálogo com a IA de uma apresentação: pedidos, perguntas e respostas."""

    class Papel(models.TextChoices):
        USUARIO = 'USUARIO', 'Você'
        IA = 'IA', 'IA'

    apresentacao = models.ForeignKey(Apresentacao, on_delete=models.CASCADE, related_name='mensagens')
    papel = models.CharField(max_length=8, choices=Papel.choices)
    texto = models.TextField(blank=True, default='')
    dados = models.JSONField(default=dict, blank=True)
    tarefa = models.ForeignKey(TarefaIA, on_delete=models.SET_NULL, null=True, blank=True, related_name='mensagens')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Mensagem com a IA'
        verbose_name_plural = 'Mensagens com a IA'
        ordering = ['criado_em', 'id']


class VersaoApresentacao(models.Model):
    """Foto do documento antes de uma mudança grande (IA refez, restauração)."""

    apresentacao = models.ForeignKey(Apresentacao, on_delete=models.CASCADE, related_name='versoes')
    documento = models.JSONField(default=dict)
    titulo = models.CharField(max_length=200, blank=True, default='')
    motivo = models.CharField(max_length=200, blank=True, default='')
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Versão de apresentação'
        verbose_name_plural = 'Versões de apresentação'
        ordering = ['-criado_em']


class ConexaoCanva(models.Model):
    """A conta do Canva de cada pessoa (OAuth). Tokens cifrados, nunca vão para a tela."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conexao_canva')
    access_token_cifrado = models.TextField(blank=True, default='')
    refresh_token_cifrado = models.TextField(blank=True, default='')
    expira_em = models.DateTimeField(null=True, blank=True)
    escopos = models.CharField(max_length=300, blank=True, default='')
    # PKCE do fluxo em andamento (a volta do Canva confere o `estado`).
    estado = models.CharField(max_length=80, blank=True, default='')
    verificador_cifrado = models.TextField(blank=True, default='')
    volta = models.CharField(max_length=300, blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Conexão com o Canva'
        verbose_name_plural = 'Conexões com o Canva'

    @property
    def conectado(self):
        return bool(self.refresh_token_cifrado or self.access_token_cifrado)
