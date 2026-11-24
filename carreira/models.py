"""Progresso — o caminho do consultor até Pleno (25/11/2026).

O consultor acompanha os 8 requisitos do Pleno; quando cumpre todos, vira um
pedido para o SUPERADMIN decidir se promove. A regra de cada requisito mora em
``carreira/regras.py``; aqui ficam só os dados que o portal ainda não tinha
(última promoção, resultado e satisfação lançados, advertências e loja apta).
"""
from django.conf import settings
from django.db import models


class ConfigCarreira(models.Model):
    """Uma linha só: se o módulo já aparece para o comercial e quais trilhas contam na Formação."""

    liberado = models.BooleanField('Liberado para o comercial', default=False, db_default=False)
    trilhas = models.ManyToManyField('knowledge_trails.KnowledgeTrail', blank=True, related_name='+',
                                     verbose_name='Trilhas da Formação')
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração do Progresso'
        verbose_name_plural = 'Configuração do Progresso'

    @classmethod
    def atual(cls):
        obj = cls.objects.filter(pk=1).first()
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class PerfilCarreira(models.Model):
    """O que o portal não sabia sozinho sobre a pessoa, e o último retrato da jornada dela."""

    JR, PLENO = 'JR', 'PLENO'
    NIVEIS = [(JR, 'Consultor Jr'), (PLENO, 'Consultor Pleno')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='perfil_carreira')
    nivel = models.CharField(max_length=8, choices=NIVEIS, default=JR, db_default=JR)
    ultima_promocao = models.DateField('Última promoção', null=True, blank=True,
                                       help_text='Sem data, conta a admissão.')
    resultado_media = models.DecimalField('Resultado: média dos últimos 6 meses (%)', max_digits=6, decimal_places=2,
                                          null=True, blank=True)
    resultado_referencia = models.CharField('Meses da média', max_length=60, blank=True, default='')
    satisfacao_nota = models.DecimalField('Satisfação do cliente (nota)', max_digits=4, decimal_places=2,
                                          null=True, blank=True)
    # Quando o automático não serve (sem dado no D-1 ou no ponto), o SUPERADMIN lança o número.
    qualidade_manual = models.DecimalField('Qualidade: % de erro (lançado)', max_digits=6, decimal_places=2,
                                           null=True, blank=True)
    assiduidade_manual = models.DecimalField('Assiduidade: % de absenteísmo (lançado)', max_digits=6,
                                             decimal_places=2, null=True, blank=True)
    promovido_em = models.DateTimeField(null=True, blank=True)
    retrato = models.JSONField('Último retrato da jornada', default=dict, blank=True)
    retrato_em = models.DateTimeField(null=True, blank=True)
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Perfil de carreira'
        verbose_name_plural = 'Perfis de carreira'

    def __str__(self):
        return f'{self.user} · {self.get_nivel_display()}'


class Advertencia(models.Model):
    """Advertência escrita (Conduta): nenhuma nos últimos 3 meses."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='advertencias_carreira')
    data = models.DateField('Data')
    motivo = models.TextField('Motivo', blank=True, default='')
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    registrado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Advertência'
        verbose_name_plural = 'Advertências'
        ordering = ['-data', '-id']


class LojaApta(models.Model):
    """Loja apta no trimestre (rentabilidade final acima de 20%). O consultor não vê o número."""

    setor = models.ForeignKey('users.Sector', on_delete=models.CASCADE, related_name='+')
    ano = models.PositiveSmallIntegerField()
    trimestre = models.PositiveSmallIntegerField()
    rentabilidade = models.DecimalField('Rentabilidade final (%)', max_digits=6, decimal_places=2, null=True,
                                        blank=True)
    apta = models.BooleanField(default=False)
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Loja apta (trimestre)'
        verbose_name_plural = 'Lojas aptas (trimestre)'
        constraints = [models.UniqueConstraint(fields=['setor', 'ano', 'trimestre'], name='carreira_loja_trimestre')]
        ordering = ['-ano', '-trimestre']


class PedidoPromocao(models.Model):
    """Cumpriu os 8 requisitos: vai para o SUPERADMIN decidir se promove."""

    ANALISE, PROMOVIDO, NAO_AGORA = 'ANALISE', 'PROMOVIDO', 'NAO_AGORA'
    SITUACOES = [(ANALISE, 'Em análise'), (PROMOVIDO, 'Promovido a Pleno'), (NAO_AGORA, 'Ainda não')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pedidos_promocao')
    situacao = models.CharField(max_length=10, choices=SITUACOES, default=ANALISE, db_default=ANALISE)
    requisitos = models.JSONField('Requisitos no dia do pedido', default=list, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    decidido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    decidido_em = models.DateTimeField(null=True, blank=True)
    parecer = models.TextField('Parecer', blank=True, default='')

    class Meta:
        verbose_name = 'Pedido de promoção'
        verbose_name_plural = 'Pedidos de promoção'
        ordering = ['-criado_em']
