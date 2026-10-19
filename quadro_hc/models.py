"""Quadro de HC loja a loja (só SUPERADMIN).

O quadro NÃO guarda as pessoas: ele lê o cadastro (``users.User``: loja =
``sector``, cargo = ``job_title``, nome). Por isso o que muda no cadastro já
aparece aqui, quem entra no portal já cai no quadro da loja dele, e o que se
muda aqui grava direto no cadastro (com o mesmo histórico de alterações).

Aqui ficam só as coisas que o cadastro não tem: as carteiras (grupos de lojas
de cada coordenação), as observações, as vagas em aberto e as movimentações —
as aplicadas (histórico) e as agendadas (com lembrete 2 dias antes).
"""
from django.conf import settings
from django.db import models


class Carteira(models.Model):
    nome = models.CharField(max_length=120)
    coordenador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='carteiras_hc')
    coordenador_nome = models.CharField('Coordenação (texto)', max_length=120, blank=True,
                                        help_text='Usado quando a coordenação não tem usuário ligado.')
    lojas = models.ManyToManyField('users.Sector', blank=True, related_name='carteiras_hc')
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['ordem', 'nome']
        verbose_name = 'Carteira'
        verbose_name_plural = 'Carteiras'

    def __str__(self):
        return self.nome

    @property
    def coordenacao(self):
        if self.coordenador_id:
            return self.coordenador.get_full_name() or self.coordenador.username
        return self.coordenador_nome


class ObservacaoHC(models.Model):
    colaborador = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='observacao_hc')
    texto = models.CharField(max_length=500, blank=True)
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Observação do quadro'
        verbose_name_plural = 'Observações do quadro'

    def __str__(self):
        return self.texto[:60]


class VagaHC(models.Model):
    setor = models.ForeignKey('users.Sector', on_delete=models.CASCADE, related_name='vagas_hc')
    cargo = models.CharField(max_length=100)
    observacao = models.CharField(max_length=300, blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    fechada_em = models.DateTimeField(null=True, blank=True)
    fechada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')

    class Meta:
        ordering = ['setor__name', 'cargo']
        verbose_name = 'Vaga em aberto'
        verbose_name_plural = 'Vagas em aberto'

    def __str__(self):
        return f'Vaga {self.cargo} · {self.setor}'


class MovimentacaoHC(models.Model):
    """Uma movimentação de quadro: aplicada na hora ou agendada para uma data."""
    AGENDADA, APLICADA, CANCELADA = 'AGENDADA', 'APLICADA', 'CANCELADA'
    SITUACOES = [(AGENDADA, 'Agendada'), (APLICADA, 'Aplicada'), (CANCELADA, 'Cancelada')]

    colaborador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                                    related_name='movimentacoes_hc')
    data = models.DateField('Data da movimentação')
    # O que muda (vazio = não muda)
    novo_setor = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    novo_cargo = models.CharField(max_length=100, blank=True)
    novo_nome = models.CharField(max_length=150, blank=True)
    desligar = models.BooleanField(default=False)
    lembrete = models.CharField('Lembrete livre', max_length=200, blank=True,
                                help_text='Movimentação que só avisa (nada muda no cadastro).')
    observacao = models.CharField(max_length=500, blank=True)
    # Como estava quando a movimentação foi criada (o histórico lê daqui)
    setor_origem = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    cargo_origem = models.CharField(max_length=100, blank=True)
    nome_origem = models.CharField(max_length=150, blank=True)
    resumo = models.CharField(max_length=300, blank=True)
    simulacao = models.JSONField(default=dict, blank=True, help_text='O resultado da simulação confirmada.')

    situacao = models.CharField(max_length=10, choices=SITUACOES, default=AGENDADA, db_index=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    aplicado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    aplicado_em = models.DateTimeField(null=True, blank=True)
    cancelado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    cancelado_em = models.DateTimeField(null=True, blank=True)
    lembrete_2d_em = models.DateTimeField(null=True, blank=True)
    lembrete_dia_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['data', 'id']
        verbose_name = 'Movimentação de quadro'
        verbose_name_plural = 'Movimentações de quadro'
        indexes = [models.Index(fields=['situacao', 'data'])]

    def __str__(self):
        return self.resumo or f'Movimentação {self.pk}'
