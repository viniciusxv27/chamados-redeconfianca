"""Modelo padrão da rotina gerencial de loja, transcrito da planilha da semana.

Legenda da planilha: vermelho = tem influência sobre o resultado (entra
travado), azul-claro = atividade complementar (a pessoa pode mover). O amarelo
("dia a dia da equipe") existe na legenda, mas nenhuma atividade da planilha
usa a cor.

As atividades estão escritas aqui mesmo, e não importadas do app: a migração
precisa reproduzir o mesmo resultado mesmo que o código do app mude depois.
"""
from datetime import time

from django.db import migrations

NOME = 'Rotina gerencial de loja (padrão)'
DESCRICAO = ('Semana-padrão de quem gere loja, transcrita da planilha de organização semanal. '
             'O que tem influência sobre o resultado entra travado; as atividades complementares '
             'a pessoa pode mover.')
R = 'RESULTADO'
C = 'COMPLEMENTAR'
QUIZ = 'https://girovquiz.com.br/'


def _hora(texto):
    hora, minuto = texto.split(':')
    return time(int(hora), int(minuto))


def atividades_do_dia_util(dia):
    """Segunda (0) a sexta (4).

    Os dias úteis são iguais, com três exceções: o matinal de segunda é com o
    diretor, terça e quinta a abertura de caixa vem com a conferência de
    preços, e quinta tem o quiz e o curso foco antes do salão.
    """
    abertura = 'Conferência/ajuste de preços · Abertura de caixa' if dia in (1, 3) else 'Abertura de caixa'
    matinal = 'Matinal com o DIRETOR' if dia == 0 else 'Matinal de alinhamento'
    itens = [
        ('08:00', '08:20', 'Envio do Fechamento D-1', R, ''),
        ('08:20', '08:40', abertura, C, ''),
        ('08:40', '09:00', matinal, R, ''),
    ]
    if dia == 3:
        itens += [
            ('09:00', '09:15', 'Quiz semanal', C, QUIZ),
            ('09:15', '09:30', 'Curso foco (se houver)', R, ''),
            ('09:30', '12:00', 'Atuação no salão', C, ''),
        ]
    else:
        itens.append(('09:00', '12:00', 'Atuação no salão', C, ''))
    itens += [
        ('12:00', '12:30', 'Parcial Gerentes', R, ''),
        ('12:30', '15:00', 'Atuação no salão', C, ''),
        ('15:00', '15:30', 'Parcial Gerentes', R, ''),
        ('15:30', '17:00', 'Atuação no salão', C, ''),
        ('17:00', '17:30', 'Conferência D-1', R, ''),
        ('17:30', '18:00', 'Atuação no salão', C, ''),
        ('18:00', '18:30', 'Fechamento de caixa · Conferência SAP x Vivo Go', R, ''),
    ]
    return itens


SABADO = [
    ('08:00', '08:20', 'Envio do Fechamento D-1', R, ''),
    ('08:20', '08:40', 'Abertura de caixa', C, ''),
    ('08:40', '09:00', 'Matinal de alinhamento', R, ''),
    ('09:00', '09:30', 'Atuação no salão', C, ''),
    ('09:30', '10:00', 'Conferência D-1', R, ''),
    ('10:00', '13:00', 'Atuação no salão', C, ''),
    ('13:00', '13:30', 'Fechamento (lojas de rua) · Fechamento de caixa · Conferência SAP x Vivo Go', R, ''),
]


def criar_modelo_padrao(apps, schema_editor):
    ModeloRotina = apps.get_model('rotina', 'ModeloRotina')
    AtividadeModelo = apps.get_model('rotina', 'AtividadeModelo')

    # Idempotente: rodar de novo (ou num banco que já tem o modelo) não duplica.
    if ModeloRotina.objects.filter(nome=NOME).exists():
        return

    modelo = ModeloRotina.objects.create(nome=NOME, descricao=DESCRICAO, ativo=True)
    novas = []
    for dia in range(6):
        itens = SABADO if dia == 5 else atividades_do_dia_util(dia)
        for inicio, fim, titulo, categoria, descricao in itens:
            novas.append(AtividadeModelo(
                modelo=modelo, dia_semana=dia, inicio=_hora(inicio), fim=_hora(fim),
                titulo=titulo, descricao=descricao, categoria=categoria,
                bloqueada=(categoria == R)))
    AtividadeModelo.objects.bulk_create(novas)


def remover_modelo_padrao(apps, schema_editor):
    ModeloRotina = apps.get_model('rotina', 'ModeloRotina')
    ModeloRotina.objects.filter(nome=NOME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rotina', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(criar_modelo_padrao, remover_modelo_padrao),
    ]
