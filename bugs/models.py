"""Bugs do portal: o colaborador relata, o Claude corrige, o SUPERADMIN aprova (24/11/2026).

Fluxo de um ``RelatoBug``:

    NA_FILA → ANALISANDO → AGUARDANDO (correção pronta e testada)
                         ↘ SEM_CORRECAO (não é defeito de código / falta informação)
                         ↘ ERRO (a análise quebrou ou parou no meio)
    AGUARDANDO → (SUPERADMIN aprova) → PUBLICANDO → PUBLICADO (commit + push no GitHub)
                                                  ↘ FALHA_PUBLICACAO (pode tentar de novo)
    AGUARDANDO → (SUPERADMIN recusa) → REJEITADO
    qualquer fim → (SUPERADMIN pede nova análise com uma orientação) → NA_FILA

A correção fica guardada como edições "troque este trecho por aquele" (``alteracoes``):
na publicação elas são reaplicadas sobre o arquivo que está no GitHub naquele momento,
então um commit feito no meio do caminho não é atropelado.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class ConfigBugs(models.Model):
    """Onde a correção aprovada é publicada (uma linha só; o SUPERADMIN configura em /bugs/configurar/)."""

    github_token_cifrado = models.TextField(blank=True, default='', verbose_name='Token do GitHub (cifrado)')
    token_hint = models.CharField(max_length=12, blank=True, default='', verbose_name='Final do token')
    repositorio = models.CharField(max_length=200, blank=True, default='', verbose_name='Repositório (dono/nome)')
    branch = models.CharField(max_length=100, blank=True, default='main', verbose_name='Branch')
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    atualizado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Configuração dos bugs'
        verbose_name_plural = 'Configuração dos bugs'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def set_token(self, raw):
        from assistente.crypto import cifrar
        raw = (raw or '').strip()
        self.github_token_cifrado = cifrar(raw) if raw else ''
        self.token_hint = ('…' + raw[-4:]) if len(raw) >= 4 else ''

    def get_token(self):
        from assistente.crypto import decifrar
        if not self.github_token_cifrado:
            return ''
        try:
            return decifrar(self.github_token_cifrado)
        except Exception:  # noqa: BLE001 — SECRET_KEY trocado: trata como sem token
            return ''


class RelatoBug(models.Model):
    class Status(models.TextChoices):
        NA_FILA = 'na_fila', 'Na fila'
        ANALISANDO = 'analisando', 'Claude analisando'
        AGUARDANDO = 'aguardando', 'Aguardando aprovação'
        SEM_CORRECAO = 'sem_correcao', 'Sem correção de código'
        ERRO = 'erro', 'Falhou na análise'
        PUBLICANDO = 'publicando', 'Publicando'
        PUBLICADO = 'publicado', 'Corrigido e publicado'
        FALHA_PUBLICACAO = 'falha_publicacao', 'Falha ao publicar'
        REJEITADO = 'rejeitado', 'Recusado'

    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bugs_relatados')
    url = models.CharField(max_length=500, verbose_name='Endereço (URL) onde aconteceu')
    descricao = models.TextField(verbose_name='O que aconteceu')
    print_tela = models.ImageField(upload_to='bugs/%Y/%m/', null=True, blank=True, verbose_name='Print da tela')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NA_FILA, db_index=True)

    # O que o Claude entregou
    titulo = models.CharField(max_length=200, blank=True, default='')
    diagnostico = models.TextField(blank=True, default='', verbose_name='Causa (Markdown)')
    solucao = models.TextField(blank=True, default='', verbose_name='O que mudou e por quê (Markdown)')
    explicacao_usuario = models.TextField(blank=True, default='', verbose_name='Explicação para quem relatou')
    alteracoes = models.JSONField(default=list, blank=True)
    diff = models.TextField(blank=True, default='')
    teste_script = models.TextField(blank=True, default='')
    teste_saida = models.TextField(blank=True, default='')
    testes_ok = models.BooleanField(null=True, blank=True)
    passos = models.JSONField(default=list, blank=True, verbose_name='Linha do tempo da análise')
    modelo = models.CharField(max_length=60, blank=True, default='')
    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_saida = models.PositiveIntegerField(default=0)
    erro = models.TextField(blank=True, default='')
    analises = models.PositiveIntegerField(default=0, verbose_name='Quantas análises já rodaram')
    orientacao = models.TextField(blank=True, default='', verbose_name='Orientação do SUPERADMIN para a nova análise')

    # Decisão e publicação
    decidido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='bugs_decididos')
    decidido_em = models.DateTimeField(null=True, blank=True)
    motivo_recusa = models.TextField(blank=True, default='')
    commit_sha = models.CharField(max_length=64, blank=True, default='')
    commit_url = models.CharField(max_length=300, blank=True, default='')
    publicado_em = models.DateTimeField(null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    analise_iniciada_em = models.DateTimeField(null=True, blank=True)
    analise_concluida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Bug relatado'
        verbose_name_plural = 'Bugs relatados'
        ordering = ['-criado_em']

    def __str__(self):
        return f'Bug #{self.pk} — {self.titulo or self.url}'

    @property
    def em_andamento(self):
        return self.status in (self.Status.NA_FILA, self.Status.ANALISANDO, self.Status.PUBLICANDO)

    @property
    def arquivos(self):
        return [a.get('caminho') for a in (self.alteracoes or [])]

    def passo(self, texto, salvar=True):
        """Linha do tempo (aparece na tela enquanto o Claude trabalha) e sinal de vida."""
        self.passos = list(self.passos or []) + [{'em': timezone.localtime().isoformat(timespec='seconds'), 'texto': texto[:300]}]
        if salvar:
            self.save(update_fields=['passos', 'atualizado_em'])
