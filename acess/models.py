"""Confiança Acess no portal: a esteira dos acessos corporativos de cada colaborador.

Adaptado do "Confiança Acess" (manual da versão 33, 17/10/2026). O que muda
aqui: quem opera é usuário do portal (sem login/senha próprios), as unidades
são os setores do portal (ADABAS do setor) e a matrícula é o mesmo
``login_code`` do cadastro. Quem tem pré-cadastro aprovado já entra na Etapa 1.

A esteira tem quatro etapas, derivadas dos campos da ficha (``etapa_calculada``):

1. Criação do GEFAC  — login ainda não válido e GEFAC ≠ OK;
2. Login inicial     — GEFAC OK e login ainda não válido;
3. Academia V        — login válido e TREINAMENTO vazio/PENDENTE;
4. Liberação e envio — entrega do login PENDENTE (depois da Academia).

Um sistema "OK" é registro operacional: o portal não testa o acesso externo.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

SISTEMAS = ['360', 'GEFAC', 'CITRIX', 'TREINAMENTO', 'NEXT', 'VIVO+', 'SAP', 'SALESFORCE', 'SIMPLIFIQUE',
            'POOL DE COBRANÇA', 'VIVO CRÉDITO', 'VIVO GED', 'SAV - RENOVA']
SISTEMAS_CHAMADO = SISTEMAS + ['AKAMAI', 'ORACLE / TOKEN']
STATUS_SISTEMA = ['', 'OK', 'PENDENTE', 'BLACKLIST']

MOTIVOS_CHAMADO = [
    ('TROCA_SENHA', 'Troca de senha'), ('DESBLOQUEIO', 'Desbloqueio de acesso'), ('FALHA', 'Falha de acesso'),
    ('LIBERACAO', 'Liberação de acesso'), ('LIBERACAO_INICIAL', 'Liberação inicial do login'),
    ('TROCA_EMAIL', 'Troca de e-mail'), ('TOKEN', 'Instalação/configuração de token'),
]


class AcessoAcess(models.Model):
    """O que o SUPERADMIN liberou para uma pessoa no Acess (lista de chaves de permissoes.FUNCOES)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='acesso_acess')
    ativo = models.BooleanField(default=True)
    permissoes = models.JSONField(default=list, blank=True)
    trata_chamados = models.BooleanField('Trata chamados (pode ser responsável)', default=False)
    observacao = models.CharField(max_length=200, blank=True)
    concedido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Acesso ao Acess'
        verbose_name_plural = 'Acessos ao Acess'


class Colaborador(models.Model):
    """A ficha de acessos de uma pessoa."""

    ATIVO, DESLIGADO = 'ATIVO', 'DESLIGADO'
    ENTREGA = [('', '—'), ('PENDENTE', 'Pendente'), ('ENVIADO', 'Enviado')]
    ORIGENS = [('PRE_CADASTRO', 'Pré-cadastro aprovado'), ('MANUAL', 'Cadastro manual'), ('PORTAL', 'Usuário do portal')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='ficha_acess', verbose_name='Usuário do portal')
    nome = models.CharField(max_length=200)
    cpf = models.CharField('CPF', max_length=11, blank=True, db_index=True)
    nascimento = models.DateField(null=True, blank=True)
    email = models.CharField(max_length=254, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    cargo = models.CharField('Cargo (PERFIL GEFAC)', max_length=100, blank=True)
    local = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                              verbose_name='Local de trabalho')
    loja_vinculo = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='Loja de vínculo')
    login = models.CharField('Login / matrícula', max_length=40, blank=True)
    sistemas = models.JSONField(default=dict, blank=True)

    etapa = models.PositiveSmallIntegerField(default=1, db_index=True)
    pendente_desde = models.DateTimeField(default=timezone.now)
    entrega_login = models.CharField(max_length=8, choices=ENTREGA, blank=True, default='')
    check_pool = models.BooleanField(default=False)
    check_perfil = models.CharField(max_length=20, blank=True, help_text='Perfil regional confirmado (ASSURANT/RENOVA_ALLIED).')
    check_login = models.BooleanField(default=False)
    check_sap = models.BooleanField(default=False)
    liberado_em = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=10, default=ATIVO, db_index=True)
    saida_empresa = models.DateField(null=True, blank=True)
    desligamento_em = models.DateField('Execução do desligamento', null=True, blank=True)
    desligamento_motivo = models.CharField(max_length=1000, blank=True)
    desligado_em = models.DateTimeField(null=True, blank=True)

    origem = models.CharField(max_length=12, choices=ORIGENS, default='MANUAL')
    versao = models.PositiveIntegerField(default=1)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'Ficha do Acess'
        verbose_name_plural = 'Fichas do Acess'

    def __str__(self):
        return self.nome

    # ── leitura ─────────────────────────────────────────────────────────────
    def status_de(self, sistema):
        return (self.sistemas or {}).get(sistema, '')

    @property
    def login_valido(self):
        valor = (self.login or '').strip()
        return bool(valor) and 'PENDENTE' not in valor.upper()

    @property
    def pendencias(self):
        """Sistemas vazios ou PENDENTE (BLACKLIST é sinalizado à parte)."""
        return [s for s in SISTEMAS if self.status_de(s) in ('', 'PENDENTE')]

    @property
    def blacklist(self):
        return [s for s in SISTEMAS if self.status_de(s) == 'BLACKLIST']

    @property
    def situacao(self):
        if self.blacklist:
            return 'BLACKLIST'
        return 'Pendente' if self.pendencias else 'OK'

    @property
    def etapa_calculada(self):
        """0 = fora da esteira (liberado ou desligado)."""
        if self.status != self.ATIVO:
            return 0
        if self.entrega_login == 'PENDENTE':
            return 4
        if self.entrega_login == 'ENVIADO':
            return 0
        if not self.login_valido:
            return 2 if self.status_de('GEFAC') == 'OK' else 1
        if self.status_de('TREINAMENTO') in ('', 'PENDENTE'):
            return 3
        return 0

    @property
    def dias_na_etapa(self):
        return max((timezone.now() - self.pendente_desde).days, 0) if self.pendente_desde else None

    @property
    def unidade_adabas(self):
        """A sede não tem ADABAS: vale o da loja de vínculo; senão o do local."""
        for setor in (self.loja_vinculo, self.local):
            if setor is not None and (setor.adabas or '').strip():
                return setor.adabas.strip()
        return ''

    @property
    def cpf_formatado(self):
        c = self.cpf or ''
        return f'{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}' if len(c) == 11 else c

    @property
    def desligamento_situacao(self):
        if self.status == self.DESLIGADO:
            return 'Desligado'
        if self.desligamento_em:
            return 'Ação manual pendente' if self.desligamento_em <= timezone.localdate() else 'Agendado'
        return ''


class Chamado(models.Model):
    ANDAMENTO, CONCLUIDO = 'ANDAMENTO', 'CONCLUIDO'

    colaborador = models.ForeignKey(Colaborador, on_delete=models.PROTECT, related_name='chamados')
    codigo = models.CharField('Nº do chamado', max_length=100)
    motivo = models.CharField(max_length=20, choices=MOTIVOS_CHAMADO)
    sistemas = models.JSONField(default=list, blank=True)
    observacao = models.TextField(max_length=3000, blank=True)
    aberto_em = models.DateTimeField(default=timezone.now)
    sla_original = models.DateTimeField()
    proxima_verificacao = models.DateTimeField()
    status = models.CharField(max_length=10, default=ANDAMENTO, db_index=True)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    concluido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    concluido_em = models.DateTimeField(null=True, blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    versao = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['-aberto_em']

    def __str__(self):
        return f'#{self.codigo} · {self.get_motivo_display()}'

    @property
    def sla_vencido(self):
        return self.status == self.ANDAMENTO and self.sla_original < timezone.now()

    @property
    def verificacao_vencida(self):
        return self.status == self.ANDAMENTO and self.proxima_verificacao < timezone.now()


class EventoChamado(models.Model):
    chamado = models.ForeignKey(Chamado, on_delete=models.CASCADE, related_name='eventos')
    quando = models.DateTimeField(default=timezone.now)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    tipo = models.CharField(max_length=12)
    detalhe = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ['quando', 'id']


class LogAcess(models.Model):
    """Histórico: quem fez o quê, quando, de onde e o antes/depois."""

    CATEGORIAS = [('CADASTRO', 'Cadastros'), ('DADOS', 'Dados'), ('TRANSFERENCIA', 'Transferências'),
                  ('CONCLUSAO', 'Conclusões'), ('CHAMADO', 'Chamados'), ('DESLIGAMENTO', 'Desligamentos'),
                  ('ADMIN', 'Administração')]

    quando = models.DateTimeField(default=timezone.now, db_index=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    usuario_nome = models.CharField(max_length=200, blank=True)
    acao = models.CharField(max_length=40, db_index=True)
    categoria = models.CharField(max_length=14, choices=CATEGORIAS, db_index=True)
    colaborador = models.ForeignKey(Colaborador, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='historico')
    descricao = models.CharField(max_length=400)
    antes = models.JSONField(null=True, blank=True)
    depois = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-quando', '-id']


class ModeloMensagem(models.Model):
    """Modelos de WhatsApp. Variáveis: {{nome}} {{login}} {{cpf3}} {{loja}} {{senha}}."""

    chave = models.CharField(max_length=30, unique=True)
    titulo = models.CharField(max_length=100)
    texto = models.TextField(max_length=4000)
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
