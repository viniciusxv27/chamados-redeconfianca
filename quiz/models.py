"""Quiz gamificado (no estilo do Kahoot) para treinamentos, desafios e competições.

- ``Quiz``: o conjunto de perguntas. Nasce rascunho; publicado, as perguntas
  travam — uma sala que já aconteceu tem de continuar apontando para as mesmas
  perguntas que foram jogadas. Para mudar um quiz publicado, duplica.
- ``Pergunta`` / ``Alternativa``: enunciado, tempo limite e de 2 a 4
  alternativas, exatamente uma correta.
- ``Sala``: uma rodada de competição de um quiz — código, data/hora e
  responsável. A partida anda por fases (``Sala.Fase``), sempre pelo relógio do
  servidor (ver ``quiz/jogo.py``).
- ``Participante``: quem foi chamado para a sala, e por qual caminho.
- ``Resposta``: o que cada participante respondeu em cada pergunta, quando e
  quantos pontos valeu.

Quem cria e administra: SUPERADMIN e quem ele liberar (chave ``quiz.gestao`` na
tela de edição do usuário). Quem joga: só quem foi selecionado para a sala.
"""
import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

TEMPO_MINIMO, TEMPO_MAXIMO = 5, 240          # segundos por pergunta
ALTERNATIVAS_MIN, ALTERNATIVAS_MAX = 2, 4
# Acerto na hora vale 1000; no último instante, 500. A velocidade pesa, mas
# acertar devagar ainda vale mais do que errar rápido (que vale zero).
PONTOS_MAXIMOS = 1000

_LETRAS_DO_CODIGO = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'   # sem 0/O, 1/I/L: o código é digitado


def gerar_codigo():
    return ''.join(secrets.choice(_LETRAS_DO_CODIGO) for _ in range(6))


class Quiz(models.Model):
    class Status(models.TextChoices):
        RASCUNHO = 'RASCUNHO', 'Rascunho'
        PUBLICADO = 'PUBLICADO', 'Publicado'

    titulo = models.CharField(max_length=150, verbose_name='Nome')
    descricao = models.TextField(blank=True, verbose_name='Descrição')
    categoria = models.CharField(max_length=80, blank=True, verbose_name='Categoria ou tema')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RASCUNHO,
                              verbose_name='Situação')
    tempo_padrao = models.PositiveSmallIntegerField(
        default=20, validators=[MinValueValidator(TEMPO_MINIMO), MaxValueValidator(TEMPO_MAXIMO)],
        verbose_name='Tempo padrão por pergunta (segundos)',
        help_text='Sugestão para cada pergunta nova; cada uma pode ter o seu.')
    duplicado_de = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='copias', verbose_name='Cópia de')
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='quizzes_criados', verbose_name='Criado por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    publicado_em = models.DateTimeField(null=True, blank=True, verbose_name='Publicado em')

    class Meta:
        verbose_name = 'Quiz'
        verbose_name_plural = 'Quizzes'
        ordering = ['-atualizado_em']

    def __str__(self):
        return self.titulo

    @property
    def publicado(self):
        return self.status == self.Status.PUBLICADO

    @property
    def editavel(self):
        """Pergunta só muda em rascunho."""
        return self.status == self.Status.RASCUNHO

    def ja_jogado(self):
        """Alguma sala dele já começou: voltar a rascunho mudaria o histórico."""
        return self.salas.exclude(fase__in=[Sala.Fase.AGENDADA, Sala.Fase.CANCELADA]).exists()

    def problemas_para_publicar(self):
        """O que impede a publicação (lista vazia: pode publicar)."""
        problemas = []
        perguntas = list(self.perguntas.prefetch_related('alternativas'))
        if not perguntas:
            problemas.append('Cadastre pelo menos uma pergunta.')
        for n, pergunta in enumerate(perguntas, start=1):
            alternativas = list(pergunta.alternativas.all())
            if len(alternativas) < ALTERNATIVAS_MIN:
                problemas.append(f'Pergunta {n}: precisa de pelo menos {ALTERNATIVAS_MIN} alternativas.')
            if sum(1 for a in alternativas if a.correta) != 1:
                problemas.append(f'Pergunta {n}: marque exatamente uma alternativa correta.')
        return problemas


class Pergunta(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='perguntas')
    ordem = models.PositiveIntegerField(default=0)
    enunciado = models.TextField(max_length=500, verbose_name='Pergunta')
    tempo_limite = models.PositiveSmallIntegerField(
        default=20, validators=[MinValueValidator(TEMPO_MINIMO), MaxValueValidator(TEMPO_MAXIMO)],
        verbose_name='Tempo para responder (segundos)')
    explicacao = models.TextField(
        max_length=500, blank=True, verbose_name='Explicação (opcional)',
        help_text='Aparece no resultado de cada participante, junto da resposta certa.')
    # Reaproveitada do banco de perguntas: a cópia é independente (mexer nela não
    # mexe no quiz de onde veio), mas fica o rastro de onde saiu.
    origem = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='reaproveitamentos', verbose_name='Reaproveitada de')
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Pergunta'
        verbose_name_plural = 'Perguntas'
        ordering = ['ordem', 'id']

    def __str__(self):
        return self.enunciado[:80]

    def alternativa_correta(self):
        return next((a for a in self.alternativas.all() if a.correta), None)


class Alternativa(models.Model):
    pergunta = models.ForeignKey(Pergunta, on_delete=models.CASCADE, related_name='alternativas')
    ordem = models.PositiveSmallIntegerField(default=0)
    texto = models.CharField(max_length=200, verbose_name='Alternativa')
    correta = models.BooleanField(default=False, verbose_name='Correta')

    class Meta:
        verbose_name = 'Alternativa'
        verbose_name_plural = 'Alternativas'
        ordering = ['ordem', 'id']

    def __str__(self):
        return self.texto


class Sala(models.Model):
    class Fase(models.TextChoices):
        AGENDADA = 'AGENDADA', 'Aguardando início'
        PERGUNTA = 'PERGUNTA', 'Pergunta no ar'
        RESULTADO = 'RESULTADO', 'Resultado da pergunta'
        ENCERRADA = 'ENCERRADA', 'Encerrada'
        CANCELADA = 'CANCELADA', 'Cancelada'

    AO_VIVO = (Fase.PERGUNTA, Fase.RESULTADO)
    ABERTAS = (Fase.AGENDADA, Fase.PERGUNTA, Fase.RESULTADO)

    quiz = models.ForeignKey(Quiz, on_delete=models.PROTECT, related_name='salas', verbose_name='Quiz')
    nome = models.CharField(max_length=150, blank=True, verbose_name='Nome da sala',
                            help_text='Opcional — sem nome, a sala usa o nome do quiz.')
    codigo = models.CharField(max_length=8, unique=True, default=gerar_codigo, verbose_name='Código')
    agendada_para = models.DateTimeField(verbose_name='Data e horário')
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                    related_name='quiz_salas', verbose_name='Responsável')
    fase = models.CharField(max_length=10, choices=Fase.choices, default=Fase.AGENDADA)
    pergunta_atual = models.PositiveSmallIntegerField(null=True, blank=True,
                                                      verbose_name='Pergunta no ar (posição)')
    pergunta_inicio = models.DateTimeField(null=True, blank=True)
    iniciada_em = models.DateTimeField(null=True, blank=True)
    encerrada_em = models.DateTimeField(null=True, blank=True)
    # Muda a cada passo da partida: as telas só redesenham quando ele muda.
    versao = models.PositiveIntegerField(default=0)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Sala'
        verbose_name_plural = 'Salas'
        ordering = ['-agendada_para']

    def __str__(self):
        return f'{self.titulo} ({self.codigo})'

    @property
    def titulo(self):
        return self.nome or self.quiz.titulo

    @property
    def aberta(self):
        return self.fase in self.ABERTAS

    @property
    def ao_vivo(self):
        return self.fase in self.AO_VIVO

    @property
    def encerrada(self):
        return self.fase == self.Fase.ENCERRADA


class Participante(models.Model):
    class Origem(models.TextChoices):
        MANUAL = 'MANUAL', 'Escolhido na lista'
        LOJA = 'LOJA', 'Pela loja'
        SETOR = 'SETOR', 'Pelo setor'
        CARGO = 'CARGO', 'Pelo cargo'
        GRUPO = 'GRUPO', 'Pelo grupo'
        COORDENACAO = 'COORDENACAO', 'Pela coordenação'

    sala = models.ForeignKey(Sala, on_delete=models.CASCADE, related_name='participantes')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='quiz_participacoes')
    origem = models.CharField(max_length=12, choices=Origem.choices, default=Origem.MANUAL)
    rotulo_origem = models.CharField(max_length=120, blank=True, verbose_name='De onde veio')
    entrou_em = models.DateTimeField(null=True, blank=True, verbose_name='Entrou na sala')
    # Última vez que a tela dela consultou a sala (gravado no máximo a cada 10 s):
    # é o "está com a sala aberta agora" do responsável.
    visto_em = models.DateTimeField(null=True, blank=True)
    # Totais da partida, somados a cada resposta — o ranking não recalcula tudo a cada segundo.
    pontos = models.PositiveIntegerField(default=0)
    acertos = models.PositiveSmallIntegerField(default=0)
    erros = models.PositiveSmallIntegerField(default=0)
    # Desempate: com os mesmos pontos, fica na frente quem somou menos tempo nos acertos.
    tempo_acertos_ms = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Participante'
        verbose_name_plural = 'Participantes'
        unique_together = [('sala', 'user')]

    def __str__(self):
        return f'{self.user} em {self.sala}'

    @property
    def nome(self):
        return self.user.get_full_name() or self.user.username


class Resposta(models.Model):
    participante = models.ForeignKey(Participante, on_delete=models.CASCADE, related_name='respostas')
    pergunta = models.ForeignKey(Pergunta, on_delete=models.CASCADE, related_name='respostas')
    alternativa = models.ForeignKey(Alternativa, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='respostas')
    # Quando a pergunta chegou na tela desta pessoa: o tempo dela conta daqui,
    # não do clique do responsável — quem recebe um segundo depois não perde por isso.
    vista_em = models.DateTimeField()
    respondida_em = models.DateTimeField(null=True, blank=True)
    tempo_ms = models.PositiveIntegerField(null=True, blank=True)
    correta = models.BooleanField(default=False)
    pontos = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Resposta'
        verbose_name_plural = 'Respostas'
        unique_together = [('participante', 'pergunta')]

    def __str__(self):
        return f'{self.participante} — {self.pergunta}'

    @property
    def respondida(self):
        return self.respondida_em is not None
