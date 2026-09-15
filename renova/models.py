"""Modelos do Vini Renova — avaliação do aparelho usado na loja.

O caminho: quem está habilitado preenche o checklist (o mesmo do impresso), o
portal abre o chamado na categoria configurada e mostra a etiqueta do aparelho.
Quem é do setor dessa categoria recebe o aparelho e marca se ele chegou.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.storage import get_media_storage

from . import checklist

# "RENOVA VINI (CONFIANÇA)", setor RENOVA: o chamado de cada Renova nasce nela.
CATEGORIA_PADRAO_ID = 89


class ConfiguracaoRenova(models.Model):
    """Configuração do módulo — uma linha só."""

    categoria = models.ForeignKey(
        'tickets.Category', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Categoria do chamado',
        help_text='Cada Renova abre um chamado nesta categoria; quem é do setor dela marca a chegada do aparelho.')
    habilitados = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='renova_habilitacoes',
        verbose_name='Quem pode fazer Renova')
    desconto_b = models.PositiveSmallIntegerField(
        default=20, validators=[MaxValueValidator(100)], verbose_name='Desconto do padrão B (%)')
    desconto_c = models.PositiveSmallIntegerField(
        default=40, validators=[MaxValueValidator(100)], verbose_name='Desconto do padrão C (%)')
    desconto_d = models.PositiveSmallIntegerField(
        default=60, validators=[MaxValueValidator(100)], verbose_name='Desconto do padrão D (%)')
    # Em branco, a tela usa os impressos que vêm com o portal (static/renova/).
    imagem_tabela = models.ImageField(
        upload_to='renova/materiais/', storage=get_media_storage(), blank=True,
        verbose_name='Imagem da tabela de avaliação')
    imagem_checklist = models.ImageField(
        upload_to='renova/materiais/', storage=get_media_storage(), blank=True,
        verbose_name='Imagem do checklist')
    imagem_passo_a_passo = models.ImageField(
        upload_to='renova/materiais/', storage=get_media_storage(), blank=True,
        verbose_name='Imagem do passo a passo')
    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        verbose_name = 'Configuração do Vini Renova'
        verbose_name_plural = 'Configuração do Vini Renova'

    def __str__(self):
        return 'Configuração do Vini Renova'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, criado = cls.objects.get_or_create(pk=1)
        if criado:
            from tickets.models import Category

            if Category.objects.filter(pk=CATEGORIA_PADRAO_ID).exists():
                obj.categoria_id = CATEGORIA_PADRAO_ID
                obj.save(update_fields=['categoria'])
        return obj

    def descontos(self):
        return {'A': 0, 'B': self.desconto_b, 'C': self.desconto_c, 'D': self.desconto_d}

    def valor_do_padrao(self, valor_a, padrao):
        """Valor de compra no padrão: A é o valor cheio; B, C e D levam o desconto configurado."""
        desconto = self.descontos().get(padrao)
        if valor_a is None or desconto is None:
            return None
        return (Decimal(valor_a) * (100 - desconto) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class PrecoAparelho(models.Model):
    """Uma linha da tabela de avaliação: modelo e armazenamento, com o valor no padrão A."""

    marca = models.CharField(max_length=12, choices=checklist.MARCAS, default='APPLE', verbose_name='Marca')
    modelo = models.CharField(max_length=80, verbose_name='Modelo')
    armazenamento = models.CharField(max_length=10, verbose_name='Armazenamento')
    valor_excelente = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], verbose_name='Valor no padrão A (R$)')
    ordem = models.PositiveIntegerField(default=0, verbose_name='Ordem')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Preço da tabela de avaliação'
        verbose_name_plural = 'Tabela de avaliação'
        ordering = ['marca', 'ordem', 'id']
        constraints = [
            models.UniqueConstraint(fields=['marca', 'modelo', 'armazenamento'], name='renova_preco_unico'),
        ]

    def __str__(self):
        return f'{self.modelo} {self.armazenamento}'

    def valores(self, cfg):
        """[(letra, nome, valor)] nos quatro padrões."""
        return [(letra, nome, cfg.valor_do_padrao(self.valor_excelente, letra)) for letra, nome in checklist.PADROES]


class Renova(models.Model):
    """Um checklist de avaliação preenchido — e o caminho do aparelho até o setor Renova."""

    PENDENTE = 'PENDENTE'
    CHEGOU = 'CHEGOU'
    NAO_CHEGOU = 'NAO_CHEGOU'
    RECEBIMENTOS = [
        (PENDENTE, 'Aguardando chegada'),
        (CHEGOU, 'Chegou'),
        (NAO_CHEGOU, 'Não chegou'),
    ]

    # 1. Dados do aparelho
    marca = models.CharField(max_length=12, choices=checklist.MARCAS, verbose_name='Marca')
    marca_outra = models.CharField(max_length=60, blank=True, verbose_name='Outra marca')
    modelo = models.CharField(max_length=120, verbose_name='Modelo')
    cor = models.CharField(max_length=60, blank=True, verbose_name='Cor')
    armazenamento = models.CharField(max_length=10, choices=checklist.ARMAZENAMENTOS, verbose_name='Armazenamento')
    armazenamento_outro = models.CharField(max_length=20, blank=True, verbose_name='Outro armazenamento')
    imei1 = models.CharField(max_length=15, db_index=True, verbose_name='IMEI 1')
    imei2 = models.CharField(max_length=15, blank=True, verbose_name='IMEI 2')
    numero_serie = models.CharField(max_length=40, blank=True, verbose_name='Nº de série')
    data_avaliacao = models.DateField(default=timezone.localdate, verbose_name='Data da avaliação')
    loja = models.ForeignKey(
        'users.Sector', on_delete=models.SET_NULL, null=True, related_name='renovas', verbose_name='Loja (origem)')
    padrao = models.CharField(max_length=1, choices=checklist.PADROES, blank=True, verbose_name='Padrão de avaliação')
    preco_tabela = models.ForeignKey(
        PrecoAparelho, on_delete=models.SET_NULL, null=True, blank=True, related_name='renovas',
        verbose_name='Linha da tabela usada')
    valor_estimado = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Valor estimado de troca (R$)')
    saude_bateria = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(100)], verbose_name='Saúde da bateria (%)')

    # 2 a 4 — respostas por item, pelas chaves de renova/checklist.py
    itens_obrigatorios = models.JSONField(default=dict, blank=True, verbose_name='Itens obrigatórios conferidos')
    funcionalidades = models.JSONField(default=dict, blank=True, verbose_name='Funcionalidades')
    estetica = models.JSONField(default=dict, blank=True, verbose_name='Condição estética')

    # 5 e 6
    observacoes = models.TextField(blank=True, verbose_name='Observações gerais')
    parecer = models.CharField(max_length=14, choices=checklist.PARECERES, verbose_name='Parecer final')

    # 7. Responsável pela avaliação
    vendedor_nome = models.CharField(max_length=150, verbose_name='Nome do vendedor')
    matricula = models.CharField(max_length=40, blank=True, verbose_name='Matrícula')
    assinatura = models.TextField(blank=True, verbose_name='Assinatura (imagem)')
    data_responsavel = models.DateField(default=timezone.localdate, verbose_name='Data')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='renovas_feitos',
        verbose_name='Feito por')
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name='Feito em')
    atualizado_em = models.DateTimeField(auto_now=True)
    chamado = models.ForeignKey(
        'tickets.Ticket', on_delete=models.SET_NULL, null=True, blank=True, related_name='renovas',
        verbose_name='Chamado')

    # Recebimento pelo setor da categoria do chamado
    recebimento = models.CharField(
        max_length=12, choices=RECEBIMENTOS, default=PENDENTE, db_index=True, verbose_name='Recebimento')
    recebido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Marcado por')
    recebido_em = models.DateTimeField(null=True, blank=True, verbose_name='Marcado em')
    recebimento_obs = models.TextField(blank=True, verbose_name='Observação do recebimento')

    class Meta:
        verbose_name = 'Renova'
        verbose_name_plural = 'Renovas'
        ordering = ['-criado_em']

    def __str__(self):
        return f'{self.codigo} — {self.aparelho}'

    @property
    def codigo(self):
        return f'RN-{self.pk:06d}' if self.pk else 'RN-NOVO'

    @property
    def marca_texto(self):
        if self.marca == 'OUTROS' and self.marca_outra:
            return self.marca_outra
        return self.get_marca_display()

    @property
    def armazenamento_texto(self):
        if self.armazenamento == 'OUTRO' and self.armazenamento_outro:
            return self.armazenamento_outro
        return self.get_armazenamento_display()

    @property
    def aparelho(self):
        return ' '.join(p for p in (self.marca_texto, self.modelo, self.armazenamento_texto) if p)

    @property
    def aprovado(self):
        return self.parecer in (checklist.APROVADO, checklist.APROVADO_OBS)

    @property
    def recebe_aparelho(self):
        """Só aparelho aprovado vira troca e chega ao setor que recebe."""
        return self.parecer != checklist.NAO_APROVADO

    @property
    def sem_recebimento(self):
        """Não aprovado e nunca marcado: o cliente ficou com o aparelho."""
        return not self.recebe_aparelho and self.recebimento == self.PENDENTE

    def itens_obrigatorios_lista(self):
        feitos = self.itens_obrigatorios or {}
        return [(chave, titulo, descricao, icone, bool(feitos.get(chave)))
                for chave, titulo, descricao, icone in checklist.ITENS_OBRIGATORIOS]

    def funcionalidades_lista(self):
        return checklist.respostas_de_itens(
            checklist.FUNCIONALIDADES, self.funcionalidades, checklist.OPCOES_FUNCIONALIDADE)

    def estetica_lista(self):
        return checklist.respostas_de_itens(checklist.ESTETICA, self.estetica, checklist.OPCOES_ESTETICA)

    @property
    def alertas(self):
        """Quantos itens de funcionalidade e estética ficaram com observação ou falha."""
        valores = list((self.funcionalidades or {}).values()) + list((self.estetica or {}).values())
        return sum(1 for v in valores if v in ('OBS', 'NAO'))
