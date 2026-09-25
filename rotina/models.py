"""Modelos da Rotina Gerencial.

A rotina é a semana-padrão de quem gere loja: de segunda a sábado, cada
atividade com o seu horário — e, nas lojas que abrem no domingo, de segunda a
domingo (`com_domingo`, no modelo e na rotina de cada pessoa). O SUPERADMIN
monta modelos (a planilha da semana), aplica um modelo a cada pessoa e, dali em
diante, a rotina da pessoa é uma cópia independente — mexer no modelo não
altera quem já recebeu.

Atividade travada tem horário fixo. As outras a própria pessoa pode arrastar
para outro horário da semana.
"""
from datetime import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.db.models import F, Q

from core.storage import get_media_storage


class Categoria(models.TextChoices):
    RESULTADO = 'RESULTADO', 'Tem influência sobre o resultado a curto e médio prazo'
    EQUIPE = 'EQUIPE', 'Tem influência no dia a dia da equipe'
    COMPLEMENTAR = 'COMPLEMENTAR', 'Atividade complementar'


# As três cores da legenda da planilha: vermelho, amarelo e azul-claro.
# `cor` é a cor forte (faixa, bolinha), `fundo` o tom claro do bloco no
# calendário e `texto` o tom escuro que dá leitura sobre esse fundo.
CORES_CATEGORIA = {
    'RESULTADO': {'nome': 'Resultado', 'cor': '#E74C3C', 'fundo': '#FDECEA', 'texto': '#8A1C12'},
    'EQUIPE': {'nome': 'Equipe', 'cor': '#F2B01E', 'fundo': '#FEF6DC', 'texto': '#7A4F00'},
    'COMPLEMENTAR': {'nome': 'Complementar', 'cor': '#4FA3E0', 'fundo': '#E6F3FC', 'texto': '#154A73'},
}

DIAS_SEMANA = [
    (0, 'Segunda-feira'),
    (1, 'Terça-feira'),
    (2, 'Quarta-feira'),
    (3, 'Quinta-feira'),
    (4, 'Sexta-feira'),
    (5, 'Sábado'),
    (6, 'Domingo'),
]
SABADO = 5
DOMINGO = 6

HORA_MINIMA = time(5, 0)
HORA_MAXIMA = time(23, 0)

# Lembrete no WhatsApp: quantos minutos antes do início, atividade por atividade
# (0 = na hora em que começa). Até 2 horas: com as atividades entre 05:00 e
# 23:00, o lembrete nunca cai no dia anterior.
MINUTOS_WHATSAPP_PADRAO = 5
MINUTOS_WHATSAPP_MAXIMO = 120


def erros_de_horario(dia_semana, inicio, fim):
    """Os problemas de dia/horário de uma atividade, em texto para a pessoa ler.

    Fica fora do modelo para a API e o `clean()` darem exatamente a mesma
    resposta — a tela não pode aceitar o que o banco vai recusar. Aqui o
    domingo vale; se a semana daquele modelo/rotina tem domingo, quem confere é
    `servicos.ler_dados_atividade`, que conhece a semana.
    """
    erros = []
    if dia_semana is None or not 0 <= dia_semana <= DOMINGO:
        erros.append('Escolha um dia de segunda a domingo.')
    if inicio is None or fim is None:
        erros.append('Informe o horário de início e de fim.')
        return erros
    if inicio < HORA_MINIMA or fim > HORA_MAXIMA:
        erros.append('Os horários precisam ficar entre 05:00 e 23:00.')
    if fim <= inicio:
        erros.append('O fim precisa ser depois do início.')
    return erros


class AtividadeBase(models.Model):
    """Campos comuns à atividade de um modelo e à atividade da rotina de alguém."""

    dia_semana = models.PositiveSmallIntegerField('Dia da semana', choices=DIAS_SEMANA)
    inicio = models.TimeField('Início')
    fim = models.TimeField('Fim')
    titulo = models.CharField('Título', max_length=150)
    descricao = models.TextField(
        'Descrição', blank=True,
        help_text='Pode ter links: eles ficam clicáveis na rotina.')
    categoria = models.CharField(
        'Categoria', max_length=20, choices=Categoria.choices, default=Categoria.COMPLEMENTAR)
    bloqueada = models.BooleanField(
        'Horário travado', default=False,
        help_text='Travada, a pessoa não consegue mover a atividade nem mudar a duração.')
    exige_comprovante = models.BooleanField(
        'Necessita comprovante', default=False, db_default=False,
        help_text='Para dar a atividade por concluída, a pessoa precisa enviar um comprovante '
                  '(foto, print ou PDF).')
    # db_default: servidor que ainda roda o código anterior (sem esta coluna) grava
    # atividade no mesmo banco — sem o default no banco, o INSERT dele falharia.
    minutos_whatsapp = models.PositiveSmallIntegerField(
        'Lembrete no WhatsApp (minutos antes)', default=MINUTOS_WHATSAPP_PADRAO,
        db_default=MINUTOS_WHATSAPP_PADRAO, validators=[MaxValueValidator(MINUTOS_WHATSAPP_MAXIMO)],
        help_text='Quanto tempo antes do início sai o lembrete no WhatsApp. 0 = na hora em que começa.')
    criada_em = models.DateTimeField('Criada em', auto_now_add=True)
    atualizada_em = models.DateTimeField('Atualizada em', auto_now=True)

    class Meta:
        abstract = True
        ordering = ['dia_semana', 'inicio', 'fim', 'id']
        # A validação de verdade é a de `erros_de_horario`; estas travas no
        # banco só garantem que nada torto entre por outro caminho (admin,
        # shell, importação).
        constraints = [
            models.CheckConstraint(
                condition=Q(fim__gt=F('inicio')),
                name='%(app_label)s_%(class)s_fim_depois_do_inicio'),
            models.CheckConstraint(
                condition=Q(dia_semana__lte=DOMINGO),
                name='%(app_label)s_%(class)s_segunda_a_domingo'),
            models.CheckConstraint(
                condition=Q(inicio__gte=HORA_MINIMA, fim__lte=HORA_MAXIMA),
                name='%(app_label)s_%(class)s_entre_5h_e_23h'),
            models.CheckConstraint(
                condition=Q(minutos_whatsapp__lte=MINUTOS_WHATSAPP_MAXIMO),
                name='%(app_label)s_%(class)s_whatsapp_ate_2h_antes'),
        ]

    def clean(self):
        erros = erros_de_horario(self.dia_semana, self.inicio, self.fim)
        if erros:
            raise ValidationError(erros)

    @property
    def cor(self):
        return CORES_CATEGORIA.get(self.categoria) or CORES_CATEGORIA['COMPLEMENTAR']

    @property
    def horario(self):
        return f'{self.inicio:%H:%M}–{self.fim:%H:%M}'

    def __str__(self):
        return f'{self.get_dia_semana_display()} {self.horario} · {self.titulo}'


class ModeloRotina(models.Model):
    """A planilha da semana, pronta para ser aplicada a várias pessoas."""

    nome = models.CharField('Nome', max_length=120)
    descricao = models.TextField('Descrição', blank=True)
    ativo = models.BooleanField(
        'Ativo', default=True,
        help_text='Inativo, o modelo sai da lista de aplicar, mas continua guardado.')
    com_domingo = models.BooleanField(
        'Semana com domingo', default=False,
        help_text='Ligado, a semana do modelo vai de segunda a domingo. Quem recebe o modelo '
                  'recebe o domingo junto.')
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Criado por')
    criado_em = models.DateTimeField('Criado em', auto_now_add=True)
    atualizado_em = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'Modelo de rotina'
        verbose_name_plural = 'Modelos de rotina'

    def __str__(self):
        return self.nome


class AtividadeModelo(AtividadeBase):
    modelo = models.ForeignKey(
        ModeloRotina, on_delete=models.CASCADE, related_name='atividades', verbose_name='Modelo')

    class Meta(AtividadeBase.Meta):
        verbose_name = 'Atividade do modelo'
        verbose_name_plural = 'Atividades do modelo'


class RotinaGerencial(models.Model):
    """A rotina de uma pessoa. Uma por pessoa; quem não tem registro não tem rotina."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rotina_gerencial',
        verbose_name='Pessoa')
    ativa = models.BooleanField(
        'Ativa', default=True,
        help_text='Desativada, a rotina some do menu da pessoa e os avisos param.')
    pode_criar = models.BooleanField(
        'Pode criar atividades próprias', default=False,
        help_text='Deixa a pessoa acrescentar (e apagar) atividades dela na semana.')
    com_domingo = models.BooleanField(
        'Semana com domingo', default=False,
        help_text='Ligado, a semana da pessoa vai de segunda a domingo, com os avisos do domingo.')
    # Ligado por padrão, como os avisos na tela e no sino: quem tem rotina é
    # avisado. Sem telefone no cadastro, simplesmente não sai nada.
    avisar_whatsapp = models.BooleanField(
        'Avisar por WhatsApp', default=True,
        help_text='Manda o lembrete de cada atividade para o WhatsApp do cadastro da pessoa '
                  '(campo Telefone), minutos antes de começar.')
    modelo_origem = models.ForeignKey(
        ModeloRotina, on_delete=models.SET_NULL, null=True, blank=True, related_name='rotinas',
        verbose_name='Modelo aplicado')
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Criada por')
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Atualizada por')
    criado_em = models.DateTimeField('Criada em', auto_now_add=True)
    atualizado_em = models.DateTimeField('Atualizada em', auto_now=True)

    class Meta:
        verbose_name = 'Rotina gerencial'
        verbose_name_plural = 'Rotinas gerenciais'

    def __str__(self):
        nome = getattr(self.user, 'full_name', '') or self.user.get_username()
        return f'Rotina de {nome}'


class AtividadeRotina(AtividadeBase):
    rotina = models.ForeignKey(
        RotinaGerencial, on_delete=models.CASCADE, related_name='atividades', verbose_name='Rotina')
    criada_pela_pessoa = models.BooleanField(
        'Criada pela própria pessoa', default=False,
        help_text='Só estas a pessoa pode renomear ou apagar — e só se a rotina permitir criar.')

    class Meta(AtividadeBase.Meta):
        verbose_name = 'Atividade da rotina'
        verbose_name_plural = 'Atividades da rotina'


def upload_comprovante(instancia, nome_do_arquivo):
    """rotina/comprovantes/2026/09/<atividade>-<aleatório>.<ext>"""
    import os
    from uuid import uuid4

    extensao = os.path.splitext(nome_do_arquivo or '')[1].lower()[:10]
    return (f'rotina/comprovantes/{instancia.data:%Y/%m}/'
            f'{instancia.atividade_id}-{uuid4().hex[:8]}{extensao}')


class ConclusaoAtividade(models.Model):
    """A pessoa deu por encerrada a atividade daquele dia.

    A rotina é semanal, mas concluir é do DIA: a mesma atividade volta na
    semana seguinte e precisa ser concluída de novo. Por isso a chave é
    (atividade, data).

    Quando a atividade está marcada como "necessita comprovante", o arquivo é
    obrigatório — é o serviço que cobra isso, não a tela.
    """

    atividade = models.ForeignKey(
        'rotina.AtividadeRotina', on_delete=models.CASCADE, related_name='conclusoes',
        verbose_name='Atividade')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rotina_conclusoes',
        verbose_name='Concluída por')
    data = models.DateField('Dia')
    comprovante = models.FileField(
        'Comprovante', upload_to=upload_comprovante, storage=get_media_storage(),
        blank=True, null=True)
    comprovante_nome = models.CharField('Nome do arquivo', max_length=255, blank=True)
    observacao = models.TextField('Observação', blank=True)
    criado_em = models.DateTimeField('Concluída em', auto_now_add=True)
    atualizado_em = models.DateTimeField('Atualizada em', auto_now=True)

    class Meta:
        ordering = ['-data', '-criado_em']
        verbose_name = 'Conclusão de atividade'
        verbose_name_plural = 'Conclusões de atividades'
        constraints = [
            models.UniqueConstraint(fields=['atividade', 'data'],
                                    name='rotina_conclusao_uma_por_atividade_e_dia'),
        ]

    def __str__(self):
        return f'{self.atividade_id} · {self.data:%d/%m/%Y}'

    @property
    def tem_comprovante(self):
        return bool(self.comprovante)

    @property
    def url_comprovante(self):
        try:
            return self.comprovante.url if self.comprovante else ''
        except Exception:                                        # noqa: BLE001
            return ''


class TipoAviso(models.TextChoices):
    LEMBRETE = 'LEMBRETE', 'Lembrete (minutos antes)'
    INICIO = 'INICIO', 'Início da atividade'


class AvisoRotina(models.Model):
    """Registro de que um aviso de uma atividade já foi dado naquele dia.

    São dois por atividade: o lembrete, minutos antes, e o do início. É o que
    torna os avisos idempotentes: várias abas, vários aparelhos e vários
    workers podem pedir o registro ao mesmo tempo, e o sino recebe um de cada.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='avisos_rotina',
        verbose_name='Pessoa')
    atividade = models.ForeignKey(
        AtividadeRotina, on_delete=models.SET_NULL, null=True, blank=True, related_name='avisos',
        verbose_name='Atividade')
    data = models.DateField('Dia')
    tipo = models.CharField('Tipo', max_length=10, choices=TipoAviso.choices, default=TipoAviso.INICIO)
    # Cópia do que foi avisado: a atividade pode ser apagada ou mudar depois.
    titulo = models.CharField('Título avisado', max_length=150, blank=True)
    inicio = models.TimeField('Início avisado', null=True, blank=True)
    criado_em = models.DateTimeField('Avisado em', auto_now_add=True)

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'Aviso de atividade'
        verbose_name_plural = 'Avisos de atividade'
        constraints = [
            models.UniqueConstraint(fields=['atividade', 'data', 'tipo'],
                                    name='rotina_aviso_um_por_atividade_dia_e_tipo'),
        ]

    def __str__(self):
        return f'{self.titulo} · {self.data:%d/%m/%Y} · {self.get_tipo_display()}'


class AvisoWhatsApp(models.Model):
    """Aviso de atividade mandado (ou tentado) pelo WhatsApp — no máximo um por atividade e dia.

    O normal é o lembrete, minutos antes do início. Se ele não saiu (atividade
    criada ou movida em cima da hora, servidor reiniciando), vai o do início;
    nunca os dois. A linha é gravada ANTES do envio e a trava única do banco é a
    reivindicação: com vários workers varrendo ao mesmo tempo, só quem consegue
    gravar manda a mensagem.

    Falha de envio (rede, instância reconectando, worker reiniciado no meio) é
    tentada de novo — até ``rotina.whatsapp.MAX_TENTATIVAS`` vezes, com uma
    espera entre elas e ainda dentro da janela do aviso. A nova tentativa também
    é reivindicada (UPDATE condicional em ``tentado_em``), então continua uma
    mensagem só. Número que o WhatsApp recusa não é repetido.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='avisos_whatsapp_rotina',
        verbose_name='Pessoa')
    atividade = models.ForeignKey(
        AtividadeRotina, on_delete=models.SET_NULL, null=True, blank=True, related_name='avisos_whatsapp',
        verbose_name='Atividade')
    data = models.DateField('Dia')
    tipo = models.CharField('Momento', max_length=10, choices=TipoAviso.choices, default=TipoAviso.LEMBRETE)
    # Cópia do que foi avisado: a atividade pode ser apagada ou mudar depois.
    titulo = models.CharField('Título avisado', max_length=150, blank=True)
    inicio = models.TimeField('Início avisado', null=True, blank=True)
    enviado = models.BooleanField('Enviado', default=False)
    detalhe = models.CharField('Retorno do envio', max_length=255, blank=True)
    # db_default: o servidor que ainda roda o código anterior (que não conhece esta
    # coluna) grava o aviso no mesmo banco sem ela — sem o default no banco, o INSERT
    # dele falhava, virava "outro worker já pegou" e o lembrete não saía.
    tentativas = models.PositiveSmallIntegerField('Tentativas', default=1, db_default=1)
    tentado_em = models.DateTimeField('Última tentativa', null=True, blank=True)
    criado_em = models.DateTimeField('Registrado em', auto_now_add=True)
    enviado_em = models.DateTimeField('Enviado em', null=True, blank=True)

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'Aviso por WhatsApp'
        verbose_name_plural = 'Avisos por WhatsApp'
        constraints = [
            models.UniqueConstraint(fields=['atividade', 'data'],
                                    name='rotina_whatsapp_um_por_atividade_e_dia'),
        ]

    def __str__(self):
        situacao = 'enviado' if self.enviado else 'não enviado'
        return f'{self.titulo} · {self.data:%d/%m/%Y} · {self.get_tipo_display()} · {situacao}'
