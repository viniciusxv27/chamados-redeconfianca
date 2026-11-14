"""Controle de estoque de uniformes da rede.

O livro de movimentos (`MovimentoUniforme`) é a fonte da verdade: cada entrada,
retirada, devolução ou ajuste vira uma linha com quem lançou, quando e — nas
retiradas e devoluções — para quem. Cada peça (tipo + modelo + tamanho) é um item
só, com dois saldos: `saldo` (estoque novo) e `saldo_usado` (quantidade usados);
cada movimento diz de qual dos dois mexe (`condicao`). Os saldos são só o
resultado do livro guardado no próprio item para a lista abrir rápido; mudam
sempre junto com a linha do movimento, na mesma transação e com o item travado
(`servicos.py`), e nunca ficam negativos. "Com colaboradores" não é saldo: sai
do livro (retiradas − devoluções).
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


# Sugestões do campo Tipo (12/11/2026: Polo, Camiseta e Boné saíram a pedido). Continua texto livre.
TIPOS_SUGERIDOS = ['Camisa', 'Calça', 'Jaqueta', 'Colete']


class AcessoUniformes(models.Model):
    """Quem o SUPERADMIN liberou para cuidar do estoque de uniformes.

    Quem está aqui lança entrada, retirada, devolução e ajuste e cadastra itens,
    mas **não** mexe nesta lista — ela continua sendo só do SUPERADMIN.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='acesso_uniformes', verbose_name='Usuário')
    observacao = models.CharField(max_length=200, blank=True, verbose_name='Motivo')
    liberado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='acessos_uniformes_liberados', verbose_name='Liberado por')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Acesso aos uniformes'
        verbose_name_plural = 'Acessos aos uniformes'
        ordering = ['user__first_name', 'user__last_name']

    def __str__(self):
        return f'{self.user} — uniformes'


class ItemUniforme(models.Model):
    """Uma peça do estoque: tipo + tamanho (+ modelo, quando houver)."""

    tipo = models.CharField(max_length=40, verbose_name='Tipo',
                            help_text='Ex.: Camisa, Calça, Jaqueta, Colete.')
    tamanho = models.CharField(max_length=10, blank=True, verbose_name='Tamanho',
                               help_text='Ex.: P, M, G, GG, 38, 40. Vazio para tamanho único.')
    descricao = models.CharField(max_length=120, blank=True, verbose_name='Modelo/descrição')
    estoque_minimo = models.PositiveIntegerField(
        default=0, verbose_name='Estoque mínimo',
        help_text='Abaixo disso o item aparece destacado na lista. 0 = sem alerta.')
    # Estoque novo (o nome do campo ficou do tempo em que só havia um saldo).
    saldo = models.IntegerField(default=0, editable=False, verbose_name='Estoque novo')
    # 14/11/2026: novo e usado no mesmo item (antes o usado era um item "… · Usado" à parte).
    # db_default: servidores com o código antigo gravam no mesmo banco sem conhecer a coluna.
    saldo_usado = models.IntegerField(default=0, db_default=0, editable=False, verbose_name='Quantidade usados')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='uniformes_itens_criados', verbose_name='Cadastrado por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='uniformes_itens_atualizados', verbose_name='Alterado por')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Item de uniforme'
        verbose_name_plural = 'Itens de uniforme'
        ordering = ['tipo', 'tamanho', 'descricao']
        constraints = [
            models.UniqueConstraint(fields=['tipo', 'tamanho', 'descricao'], name='uniforme_item_unico'),
            models.CheckConstraint(condition=models.Q(saldo__gte=0), name='uniforme_saldo_nao_negativo'),
            models.CheckConstraint(condition=models.Q(saldo_usado__gte=0), name='uniforme_saldo_usado_nao_negativo'),
        ]

    def __str__(self):
        return self.nome

    @property
    def nome(self):
        partes = [self.tipo]
        if self.descricao:
            partes.append(self.descricao)
        if self.tamanho:
            partes.append(self.tamanho)
        return ' · '.join(partes)

    @property
    def total_estoque(self):
        """Peças na prateleira: novas + usadas."""
        return (self.saldo or 0) + (self.saldo_usado or 0)

    def saldo_de(self, condicao):
        return self.saldo_usado if condicao == MovimentoUniforme.USADO else self.saldo

    @property
    def abaixo_do_minimo(self):
        """O mínimo vale para o que há na prateleira (novas + usadas)."""
        return bool(self.estoque_minimo) and self.total_estoque < self.estoque_minimo


class MovimentoUniforme(models.Model):
    """Uma linha do livro do estoque."""

    ENTRADA = 'ENTRADA'
    RETIRADA = 'RETIRADA'
    DEVOLUCAO = 'DEVOLUCAO'
    AJUSTE = 'AJUSTE'
    TIPO_CHOICES = [
        (ENTRADA, 'Entrada'),
        (RETIRADA, 'Retirada (entrega ao colaborador)'),
        (DEVOLUCAO, 'Devolução'),
        (AJUSTE, 'Ajuste de inventário'),
    ]

    NOVO = 'NOVO'
    USADO = 'USADO'
    CONDICAO_CHOICES = [(NOVO, 'Novo'), (USADO, 'Usado')]

    item = models.ForeignKey(ItemUniforme, on_delete=models.PROTECT,
                             related_name='movimentos', verbose_name='Item')
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, verbose_name='Movimento')
    # De qual saldo do item o lançamento mexe: NOVO → `saldo`, USADO → `saldo_usado`.
    condicao = models.CharField(max_length=5, choices=CONDICAO_CHOICES, default=NOVO, db_default=NOVO,
                                verbose_name='Condição da peça')
    quantidade = models.PositiveIntegerField(verbose_name='Quantidade')
    # Efeito no estoque (+ entra, − sai): o saldo da condição é a soma desta coluna nas linhas dela.
    delta = models.IntegerField(verbose_name='Efeito no estoque')
    # Saldo da condição do movimento (novo ou usado) logo depois dele.
    saldo_apos = models.IntegerField(verbose_name='Saldo depois')

    colaborador = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name='uniformes_movimentos', verbose_name='Para quem')
    # Retirada: dia em que o uniforme chegou na mão do colaborador.
    # Entrada: dia do recebimento. Devolução: dia em que voltou.
    data = models.DateField(null=True, blank=True, verbose_name='Data')
    nota_fiscal = models.CharField(max_length=60, blank=True, verbose_name='Nota fiscal')
    observacao = models.TextField(blank=True, verbose_name='Observação / motivo')
    # Linhas lançadas juntas (uma retirada com camisa e calça) dividem o lote: é o termo.
    lote = models.UUIDField(default=uuid.uuid4, db_index=True, verbose_name='Lote')

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='uniformes_movimentos_lancados', verbose_name='Lançado por')
    registrado_em = models.DateTimeField(default=timezone.now, verbose_name='Lançado em')
    data_alterada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='uniformes_datas_alteradas', verbose_name='Data alterada por')
    data_alterada_em = models.DateTimeField(null=True, blank=True, verbose_name='Data alterada em')

    class Meta:
        verbose_name = 'Movimento de uniforme'
        verbose_name_plural = 'Movimentos de uniforme'
        ordering = ['-registrado_em', '-id']
        indexes = [
            models.Index(fields=['colaborador', 'tipo'], name='uniforme_mov_colab_tipo'),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.item} ({self.rotulo_condicao}) · {self.quantidade}'

    @property
    def usado(self):
        return self.condicao == self.USADO

    @property
    def rotulo_condicao(self):
        """"nova"/"usada" — a peça, no feminino (camisa, calça, jaqueta)."""
        return 'usada' if self.condicao == self.USADO else 'nova'

    @property
    def rotulo_curto(self):
        return {self.ENTRADA: 'Entrada', self.RETIRADA: 'Retirada',
                self.DEVOLUCAO: 'Devolução', self.AJUSTE: 'Ajuste'}.get(self.tipo, self.tipo)
