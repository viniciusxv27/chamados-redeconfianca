from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from core.utils import upload_prize_image

def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


class PrizeCategory(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nome da Categoria")
    description = models.TextField(blank=True, verbose_name="Descrição")
    icon = models.CharField(max_length=50, default="fas fa-gift", verbose_name="Ícone Font Awesome")
    color = models.CharField(max_length=20, default="blue", verbose_name="Cor")
    active = models.BooleanField(default=True, verbose_name="Ativa")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Categoria de Prêmio"
        verbose_name_plural = "Categorias de Prêmios"
        ordering = ['name']
    
    def __str__(self):
        return self.name


# Resgates que ocupam uma unidade do estoque: tudo, menos o cancelado (o cancelamento
# devolve a unidade). O pendente conta para ninguém resgatar a última unidade duas vezes
# enquanto o primeiro espera aprovação. Uma régua só para /prizes/, /prizes/manage/,
# /prizes/<id>/edit/ e o app (26/11/2026).
STATUS_QUE_OCUPAM_ESTOQUE = ('PENDENTE', 'APROVADO', 'ENTREGUE')


LIMITE_CAMPOS_RESGATE = 10
LIMITE_NOME_CAMPO = 60
LIMITE_RESPOSTA = 500


def normalizar_campos(campos):
    """Lista de campos do prêmio sem nome vazio nem repetido, no formato
    [{'nome': str, 'obrigatorio': bool}]. Aceita o JSON gravado ou o que veio da tela."""
    saida, vistos = [], set()
    for c in campos or []:
        if isinstance(c, str):
            c = {'nome': c, 'obrigatorio': True}
        if not isinstance(c, dict):
            continue
        nome = ' '.join(str(c.get('nome') or '').split())[:LIMITE_NOME_CAMPO]
        if not nome or nome.lower() in vistos:
            continue
        vistos.add(nome.lower())
        saida.append({'nome': nome, 'obrigatorio': bool(c.get('obrigatorio', True))})
        if len(saida) == LIMITE_CAMPOS_RESGATE:
            break
    return saida


def campos_do_formulario(post):
    """Campos digitados em /prizes/create/ e /prizes/<id>/edit/: cada linha manda
    campo_nome e campo_obrigatorio ("1"/"0") na mesma ordem."""
    nomes = post.getlist('campo_nome')
    obrig = post.getlist('campo_obrigatorio')
    return normalizar_campos([
        {'nome': n, 'obrigatorio': (obrig[i] if i < len(obrig) else '1') == '1'}
        for i, n in enumerate(nomes)
    ])


def respostas_do_resgate(campos, dados):
    """Confere o que quem resgata preencheu. ``dados`` é o POST (resposta_0,
    resposta_1...) ou um dict do app. Devolve (respostas, erro)."""
    respostas, faltando = [], []
    for i, campo in enumerate(campos):
        valor = dados.get(f'resposta_{i}') if hasattr(dados, 'get') else None
        valor = ' '.join(str(valor or '').split())[:LIMITE_RESPOSTA]
        if not valor and campo['obrigatorio']:
            faltando.append(campo['nome'])
        respostas.append({'nome': campo['nome'], 'valor': valor})
    if faltando:
        return None, 'Preencha: ' + ', '.join(faltando) + '.'
    return respostas, None


class PrizeQuerySet(models.QuerySet):
    def visiveis(self):
        """Fora os excluídos da gestão (exclusão lógica: os resgates continuam)."""
        return self.filter(excluido_em__isnull=True)

    def com_resgates(self):
        """Anota as contagens de resgate de uma vez (sem uma consulta por prêmio)."""
        from django.db.models import Count, Q
        return self.annotate(
            n_resgates_ativos=Count('redemption', filter=Q(redemption__status__in=STATUS_QUE_OCUPAM_ESTOQUE), distinct=True),
            n_pendentes=Count('redemption', filter=Q(redemption__status='PENDENTE'), distinct=True),
            n_aprovados=Count('redemption', filter=Q(redemption__status='APROVADO'), distinct=True),
            n_entregues=Count('redemption', filter=Q(redemption__status='ENTREGUE'), distinct=True),
            n_cancelados=Count('redemption', filter=Q(redemption__status='CANCELADO'), distinct=True),
        )


class Prize(models.Model):
    PRIORITY_CHOICES = [
        ('BAIXA', 'Baixa'),
        ('NORMAL', 'Normal'),
        ('ALTA', 'Alta'),
        ('DESTAQUE', 'Destaque'),
    ]
    
    name = models.CharField(max_length=200, verbose_name="Nome")
    description = models.TextField(verbose_name="Descrição")
    category = models.ForeignKey(PrizeCategory, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Categoria")
    value_cs = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor em C$")
    
    # Desconto específico do prêmio
    has_discount = models.BooleanField(default=False, verbose_name="Tem Desconto")
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Desconto (%)", help_text="Porcentagem de desconto (ex: 10 para 10%)")
    discounted_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Valor com Desconto", help_text="Valor final após aplicar o desconto")
    
    image = models.ImageField(upload_to=upload_prize_image, storage=get_media_storage(), blank=True, null=True, verbose_name="Imagem")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='NORMAL', verbose_name="Prioridade")
    stock = models.PositiveIntegerField(default=0, verbose_name="Estoque")
    unlimited_stock = models.BooleanField(default=False, verbose_name="Estoque Ilimitado")
    redeemed_count = models.PositiveIntegerField(default=0, verbose_name="Quantidade Resgatada")
    terms = models.TextField(blank=True, verbose_name="Termos e Condições")
    valid_until = models.DateField(blank=True, null=True, verbose_name="Válido até")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name="Criado por")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Exclusão lógica (26/11/2026): some da gestão e do Mercadinho, mas o prêmio fica salvo
    # para o histórico de quem já resgatou.
    excluido_em = models.DateTimeField(null=True, blank=True, verbose_name="Excluído em")
    excluido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name="Excluído por")
    # Campos que quem resgata precisa preencher (27/11/2026): quem cadastra digita o nome
    # que quiser ("Numeração", "Observação", "Cor"...). Lista de {"nome", "obrigatorio"};
    # as respostas ficam no Redemption.respostas. db_default: outros servidores rodam o
    # código antigo no mesmo banco e inserem sem essa coluna.
    campos_resgate = models.JSONField(default=list, blank=True, db_default=[],
                                      verbose_name="Campos pedidos no resgate")

    objects = PrizeQuerySet.as_manager()

    class Meta:
        verbose_name = "Prêmio"
        verbose_name_plural = "Prêmios"
        ordering = ['-priority', 'name']
    
    def __str__(self):
        return f"{self.name} - C$ {self.value_cs}"
    
    @property
    def final_value(self):
        """Retorna o valor final (com desconto se aplicável)"""
        if self.has_discount and self.discounted_value:
            return self.discounted_value
        return self.value_cs
    
    @property
    def discount_amount(self):
        """Retorna o valor do desconto"""
        if self.has_discount and self.discounted_value:
            return self.value_cs - self.discounted_value
        return Decimal('0.00')
    
    @property
    def resgates_ativos(self):
        """Resgates que ocupam o estoque (todos, menos os cancelados). Usa a anotação de
        PrizeQuerySet.com_resgates quando existe; senão conta no banco."""
        n = getattr(self, 'n_resgates_ativos', None)
        if n is None:
            n = self.redemption_set.filter(status__in=STATUS_QUE_OCUPAM_ESTOQUE).count()
        return n

    @property
    def estoque_restante(self):
        """Unidades que ainda dá para resgatar (None = ilimitado)."""
        if self.unlimited_stock:
            return None
        return max(0, self.stock - self.resgates_ativos)

    @property
    def esgotado(self):
        return not self.unlimited_stock and self.estoque_restante == 0

    @property
    def excluido(self):
        return self.excluido_em is not None

    @property
    def campos(self):
        """Campos do resgate já limpos: [{'nome': 'Numeração', 'obrigatorio': True}, ...]."""
        return normalizar_campos(self.campos_resgate)

    @property
    def available(self):
        if not self.is_active or self.excluido:
            return False
        if self.valid_until and self.valid_until < timezone.now().date():
            return False
        if self.esgotado:
            return False
        return True

    @property
    def availability_percentage(self):
        if self.unlimited_stock:
            return 100
        if self.stock == 0:
            return 0
        return (self.estoque_restante / self.stock) * 100


class PrizeDiscount(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('PERCENTAGE', 'Porcentagem'),
        ('FIXED', 'Valor Fixo'),
    ]
    
    name = models.CharField(max_length=200, verbose_name="Nome do Desconto")
    code = models.CharField(max_length=50, unique=True, verbose_name="Código", help_text="Código único para o desconto")
    description = models.TextField(blank=True, verbose_name="Descrição")
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES, default='PERCENTAGE', verbose_name="Tipo de Desconto")
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor do Desconto", help_text="Porcentagem (ex: 10 para 10%) ou valor fixo em C$")
    min_purchase_value = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Valor Mínimo de Compra", help_text="Valor mínimo em C$ para aplicar o desconto")
    max_discount_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Desconto Máximo", help_text="Valor máximo de desconto em C$ (apenas para porcentagem)")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    valid_from = models.DateField(verbose_name="Válido de")
    valid_until = models.DateField(verbose_name="Válido até")
    max_uses = models.PositiveIntegerField(null=True, blank=True, verbose_name="Máximo de Usos", help_text="Número máximo de vezes que pode ser usado (deixe vazio para ilimitado)")
    uses_count = models.PositiveIntegerField(default=0, verbose_name="Quantidade de Usos")
    applies_to_categories = models.ManyToManyField(PrizeCategory, blank=True, verbose_name="Categorias Aplicáveis", help_text="Deixe vazio para aplicar a todos os prêmios")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name="Criado por")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Desconto"
        verbose_name_plural = "Descontos"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} ({self.code})"
    
    @property
    def is_valid(self):
        """Verifica se o desconto está válido"""
        if not self.is_active:
            return False
        today = timezone.now().date()
        if self.valid_from > today or self.valid_until < today:
            return False
        if self.max_uses and self.uses_count >= self.max_uses:
            return False
        return True
    
    def can_apply_to_prize(self, prize):
        """Verifica se o desconto pode ser aplicado ao prêmio"""
        if not self.is_valid:
            return False
        if self.applies_to_categories.exists():
            return prize.category in self.applies_to_categories.all()
        return True
    
    def calculate_discount(self, prize_value):
        """Calcula o valor do desconto"""
        if prize_value < self.min_purchase_value:
            return Decimal('0.00')
        
        if self.discount_type == 'PERCENTAGE':
            discount = (prize_value * self.discount_value) / Decimal('100')
            if self.max_discount_value:
                discount = min(discount, self.max_discount_value)
            return discount
        else:  # FIXED
            return min(self.discount_value, prize_value)


class Redemption(models.Model):
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('APROVADO', 'Aprovado'),
        ('ENTREGUE', 'Entregue'),
        ('CANCELADO', 'Cancelado'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    prize = models.ForeignKey(Prize, on_delete=models.CASCADE, verbose_name="Prêmio")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDENTE', verbose_name="Status")
    discount = models.ForeignKey(PrizeDiscount, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Desconto Aplicado")
    original_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Valor Original")
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Valor do Desconto")
    final_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Valor Final")
    redeemed_at = models.DateTimeField(auto_now_add=True, verbose_name="Data do Resgate")
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="Data da Aprovação")
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name="Data da Entrega")
    delivery_notes = models.TextField(blank=True, verbose_name="Observações de Entrega")
    # Retirada na SEDE
    pickup_deadline = models.DateField(null=True, blank=True, verbose_name="Data Limite para Retirada")
    pickup_notified_at = models.DateTimeField(null=True, blank=True, verbose_name="Notificado para Retirada em")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='approved_redemptions',
        verbose_name="Aprovado por"
    )
    notes = models.TextField(blank=True, verbose_name="Observações")
    # O que quem resgatou preencheu nos campos do prêmio: [{"nome", "valor"}].
    respostas = models.JSONField(default=list, blank=True, db_default=[],
                                 verbose_name="Informações do resgate")
    
    class Meta:
        verbose_name = "Resgate"
        verbose_name_plural = "Resgates"
        ordering = ['-redeemed_at']
    
    def __str__(self):
        return f"{self.user.full_name} - {self.prize.name}"

    @property
    def respostas_preenchidas(self):
        """Só as respostas com valor, para mostrar na gestão e no histórico."""
        return [r for r in (self.respostas or []) if isinstance(r, dict) and r.get('valor')]

    @property
    def respostas_texto(self):
        return ' · '.join(f"{r.get('nome')}: {r.get('valor')}" for r in self.respostas_preenchidas)

    @property
    def awaiting_pickup(self):
        """Resgate aprovado, aguardando retirada na SEDE (usuário já notificado)."""
        return (
            self.status == 'APROVADO'
            and self.pickup_deadline is not None
            and self.pickup_notified_at is not None
        )


class CSTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('CREDIT', 'Crédito'),
        ('DEBIT', 'Débito'),
        ('REDEMPTION', 'Resgate'),
        ('REFUND', 'Reembolso'),
        ('ADJUSTMENT', 'Ajuste'),
    ]
    
    STATUS_CHOICES = [
        ('PENDING', 'Pendente'),
        ('APPROVED', 'Aprovado'),
        ('REJECTED', 'Rejeitado'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor")
    transaction_type = models.CharField(max_length=15, choices=TRANSACTION_TYPES, verbose_name="Tipo")
    description = models.CharField(max_length=200, verbose_name="Descrição")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='APPROVED', verbose_name="Status")
    related_redemption = models.ForeignKey(
        Redemption, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Resgate Relacionado"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_transactions',
        verbose_name="Criado por"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_transactions',
        verbose_name="Aprovado por"
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="Data de Aprovação")
    rejection_reason = models.TextField(blank=True, verbose_name="Motivo da Rejeição")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data")
    
    # Data de validade das confianças
    expiration_date = models.DateField(
        null=True, 
        blank=True, 
        verbose_name="Data de Validade",
        help_text="Data em que as confianças irão expirar. Deixe em branco para não expirar."
    )
    expiration_notified_7_days = models.BooleanField(default=False, verbose_name="Notificado 7 dias antes")
    expiration_notified_3_days = models.BooleanField(default=False, verbose_name="Notificado 3 dias antes")
    expiration_notified_1_day = models.BooleanField(default=False, verbose_name="Notificado 1 dia antes")
    
    class Meta:
        verbose_name = "Transação C$"
        verbose_name_plural = "Transações C$"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.full_name} - {self.transaction_type} - C$ {self.amount}"
    
    @property
    def is_expired(self):
        """Verifica se a transação expirou"""
        if not self.expiration_date:
            return False
        from django.utils import timezone
        return timezone.now().date() > self.expiration_date
    
    @property
    def days_until_expiration(self):
        """Retorna o número de dias até a expiração"""
        if not self.expiration_date:
            return None
        from django.utils import timezone
        delta = self.expiration_date - timezone.now().date()
        return delta.days


class SugestaoProduto(models.Model):
    """Link de produto que o colaborador sugere para o Mercadinho; a empresa analisa se é viável."""
    PENDENTE, APROVADA, RECUSADA = 'PENDENTE', 'APROVADA', 'RECUSADA'
    STATUS = [(PENDENTE, 'Em análise'), (APROVADA, 'Aprovada'), (RECUSADA, 'Não incluída')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sugestoes_mercadinho', verbose_name='Quem sugeriu')
    link = models.URLField(max_length=2000, verbose_name='Link do produto')
    nome = models.CharField(max_length=150, blank=True, verbose_name='Nome do produto')
    observacao = models.CharField(max_length=500, blank=True, verbose_name='Por que sugerir')
    status = models.CharField(max_length=10, choices=STATUS, default=PENDENTE, db_index=True, verbose_name='Situação')
    resposta = models.CharField(max_length=500, blank=True, verbose_name='Resposta')
    analisado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    analisado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Sugestão de produto'
        verbose_name_plural = 'Sugestões de produto'
        ordering = ['-criado_em']

    def __str__(self):
        return self.nome or self.link
