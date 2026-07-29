"""Fechamento de mês e encerramento de ciclo do Impulso.

Fluxo:
  1. Gestor inicia um ciclo (normalmente 3 meses) -> cria os CicloMes.
  2. A cada mês, "finalizar mês" congela a pontuação de todos (PontuacaoMensal),
     define a faixa e reserva as confianças de quem ficou Ouro/Impulso.
  3. Ao "encerrar ciclo", as confianças acumuladas são creditadas de fato (C$)
     e o ciclo passa a exibir nota total e sequência de medalhas.
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from .models import CicloMes, PontuacaoMensal
from .scoring import calcular_pontuacao
from .utils import (CONFIANCAS_POR_MES, FAIXAS_PREMIADAS, faixa_por_score,
                    get_colaboradores)

User = get_user_model()


def periodo_do_mes_obj(mes):
    ref = mes.referencia
    inicio = date(ref.year, ref.month, 1)
    fim = date(ref.year, ref.month, monthrange(ref.year, ref.month)[1])
    return inicio, fim


def meses_entre(inicio, fim):
    """Lista de primeiros-dias-de-mês entre duas datas (inclusive)."""
    meses = []
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        meses.append(date(ano, mes, 1))
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return meses


def criar_meses(ciclo):
    """Cria os CicloMes do período do ciclo (idempotente)."""
    criados = 0
    for referencia in meses_entre(ciclo.inicio, ciclo.fim):
        _, novo = CicloMes.objects.get_or_create(ciclo=ciclo, referencia=referencia)
        criados += 1 if novo else 0
    return criados


@transaction.atomic
def fechar_mes(mes, usuario):
    """Congela a pontuação de todos os colaboradores no mês."""
    inicio, fim = periodo_do_mes_obj(mes)
    total_pessoas = 0

    for colaborador in get_colaboradores():
        dados = calcular_pontuacao(colaborador, inicio=inicio, fim=fim)
        faixa = dados['faixa']
        premio = CONFIANCAS_POR_MES if faixa in FAIXAS_PREMIADAS else 0

        PontuacaoMensal.objects.update_or_create(
            mes=mes, user=colaborador,
            defaults={
                'setor': getattr(colaborador, 'sector', None),
                'p_metas_qualidade': dados['p_metas_qualidade'],
                'p_metas_conclusao': dados['p_metas_conclusao'],
                'p_feedback': dados['p_feedback'],
                'p_assiduidade': dados['p_assiduidade'],
                'p_curso': dados['p_curso'],
                'p_videos_pops': dados['p_videos_pops'],
                'p_projeto_foco': dados['p_projeto_foco'],
                'p_ideias': dados['p_ideias'],
                'p_ideia_aprovada': dados['p_ideia_aprovada'],
                'total': dados['total'],
                'pontos_aplicaveis': dados['aplicavel'],
                'percentual': dados['percentual'],
                'faixa': faixa,
                'confiancas_previstas': premio,
                'detalhes': dados['detalhes'],
            },
        )
        total_pessoas += 1

    mes.status = CicloMes.Status.FECHADO
    mes.fechado_em = timezone.now()
    mes.fechado_por = usuario
    mes.save(update_fields=['status', 'fechado_em', 'fechado_por'])
    return total_pessoas


def reabrir_mes(mes):
    mes.status = CicloMes.Status.ABERTO
    mes.fechado_em = None
    mes.fechado_por = None
    mes.save(update_fields=['status', 'fechado_em', 'fechado_por'])


def setores_do_mes(mes):
    """Setor Destaque: soma das notas dos colaboradores por setor principal."""
    linhas = (PontuacaoMensal.objects.filter(mes=mes)
              .values('setor__id', 'setor__name')
              .annotate(soma=Sum('total'), media=Avg('percentual'), pessoas=Count('id'))
              .order_by('-soma'))
    return [{
        'setor_id': l['setor__id'],
        'setor': l['setor__name'] or 'Sem setor',
        'soma': l['soma'] or Decimal('0'),
        'media': round(l['media'] or 0, 1),
        'pessoas': l['pessoas'],
    } for l in linhas]


def resumo_ciclo(ciclo):
    """Nota total e sequência de medalhas por colaborador no ciclo."""
    pontuacoes = (PontuacaoMensal.objects
                  .filter(mes__ciclo=ciclo)
                  .select_related('user', 'mes', 'setor')
                  .order_by('mes__referencia'))

    por_usuario = {}
    for p in pontuacoes:
        linha = por_usuario.setdefault(p.user_id, {
            'user': p.user, 'meses': [], 'soma': Decimal('0'),
            'soma_percentual': Decimal('0'), 'confiancas': 0,
        })
        linha['meses'].append(p)
        linha['soma'] += p.total
        linha['soma_percentual'] += p.percentual
        linha['confiancas'] += p.confiancas_previstas

    resultado = []
    for linha in por_usuario.values():
        qtd = len(linha['meses']) or 1
        media = linha['soma_percentual'] / qtd
        resultado.append({
            'user': linha['user'],
            'meses': linha['meses'],
            'total': linha['soma'],
            'media_percentual': round(media, 1),
            'faixa': faixa_por_score(media),
            'confiancas': linha['confiancas'],
        })
    resultado.sort(key=lambda l: l['media_percentual'], reverse=True)
    return resultado


@transaction.atomic
def creditar_confiancas(ciclo, usuario):
    """Credita as confianças acumuladas do ciclo (Ouro/Impulso). Idempotente."""
    if ciclo.confiancas_creditadas:
        return []

    from prizes.models import CSTransaction

    totais = (PontuacaoMensal.objects
              .filter(mes__ciclo=ciclo, confiancas_previstas__gt=0)
              .values('user')
              .annotate(total=Sum('confiancas_previstas')))

    creditados = []
    for linha in totais:
        valor = Decimal(str(linha['total'] or 0))
        if valor <= 0:
            continue
        colaborador = User.objects.filter(id=linha['user']).first()
        if not colaborador:
            continue
        # Saldo é campo armazenado: atualizar E registrar a transação.
        colaborador.balance_cs = (colaborador.balance_cs or Decimal('0')) + valor
        colaborador.save(update_fields=['balance_cs'])
        CSTransaction.objects.create(
            user=colaborador,
            amount=valor,
            transaction_type='CREDIT',
            description=f'Impulso — prêmio do ciclo {ciclo.nome}',
            status='APPROVED',
            created_by=usuario,
        )
        creditados.append({'user': colaborador, 'valor': valor})

    ciclo.confiancas_creditadas = True
    ciclo.save(update_fields=['confiancas_creditadas'])
    return creditados


@transaction.atomic
def encerrar_ciclo(ciclo, usuario):
    """Encerra o ciclo e credita as confianças acumuladas."""
    creditados = creditar_confiancas(ciclo, usuario)
    ciclo.status = ciclo.Status.ENCERRADO
    ciclo.encerrado_em = timezone.now()
    ciclo.encerrado_por = usuario
    ciclo.save(update_fields=['status', 'encerrado_em', 'encerrado_por'])
    return creditados
