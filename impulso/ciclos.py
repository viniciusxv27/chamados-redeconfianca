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


def _para_json(valor):
    """Deixa o detalhamento gravável num JSONField.

    O cálculo ao vivo trabalha com os tipos do Python — `date` nos dias de
    falta, `Decimal` nas notas — e a tela lida bem com isso. O snapshot vai
    para uma coluna JSON, e aí `date` levanta `TypeError: Object of type date
    is not JSON serializable` **no meio da transação**: o mês não fechava, e a
    tela só mostrava erro. Converte aqui, uma vez, na fronteira do banco.
    """
    from datetime import date as _date, datetime as _datetime
    from decimal import Decimal as _Decimal

    if isinstance(valor, dict):
        return {str(k): _para_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple, set)):
        return [_para_json(v) for v in valor]
    if isinstance(valor, _datetime):
        return valor.strftime('%d/%m/%Y %H:%M')
    if isinstance(valor, _date):
        return valor.strftime('%d/%m/%Y')
    if isinstance(valor, _Decimal):
        return float(valor)
    return valor


# ---------------------------------------------------------------------------
# Ajuste manual (SUPERADMIN) de um mês fechado
# ---------------------------------------------------------------------------
# Quem ficou "por pouco" (89,5% e a Prata no lugar do Ouro, um curso feito no dia
# seguinte ao prazo…) é corrigido aqui, item a item, com o motivo registrado. O
# total, o percentual, a faixa e as C$ do mês saem de novo da mesma conta do
# fechamento, e o ajuste sobrevive a reabrir e fechar o mês de novo.
CAMPOS_PONTOS = (
    ('p_metas_qualidade', 'metas_qualidade', 'Metas — qualidade'),
    ('p_metas_conclusao', 'metas_conclusao', 'Metas — conclusão'),
    ('p_feedback', 'feedback', 'Feedback'),
    ('p_assiduidade', 'assiduidade', 'Assiduidade'),
    ('p_curso', 'curso', 'Curso do mês'),
    ('p_videos_pops', 'videos_pops', 'Vídeos e POPs'),
    ('p_projeto_foco', 'projeto_foco', 'Projeto foco'),
    ('p_ideias', 'ideias', 'Ideias propostas'),
    ('p_ideia_aprovada', 'ideia_aprovada', 'Ideia aprovada'),
)


class AjusteRecusado(Exception):
    """Ajuste que não pode ser feito (ciclo já pago, valor inválido, sem motivo)."""


def maximos_do_item(user):
    from .scoring import pesos
    tabela = pesos(user)
    return {campo: tabela.get(peso, Decimal('0')) for campo, peso, _ in CAMPOS_PONTOS}


def itens_da_pontuacao(pontuacao):
    """As linhas do mês fechado para a tela: rótulo, pontos, máximo e o valor calculado."""
    maximos = maximos_do_item(pontuacao.user)
    calculado = ((pontuacao.detalhes or {}).get('ajuste_manual') or {}).get('calculado') or {}
    linhas = []
    for campo, _, rotulo in CAMPOS_PONTOS:
        valor = getattr(pontuacao, campo)
        original = calculado.get(campo)
        linhas.append({
            'campo': campo, 'rotulo': rotulo, 'valor': valor, 'max': maximos[campo],
            'calculado': original,
            'mudou': original is not None and Decimal(str(original)) != Decimal(valor),
        })
    return linhas


def _recalcular(pontuacao):
    """Total, percentual, faixa e C$ a partir dos itens — a mesma conta do fechamento."""
    total = sum((Decimal(getattr(pontuacao, campo)) for campo, _, _ in CAMPOS_PONTOS), Decimal('0'))
    aplicavel = Decimal(pontuacao.pontos_aplicaveis or 0)
    percentual = total / aplicavel * 100 if aplicavel else Decimal('0')
    pontuacao.total = total.quantize(Decimal('0.01'))
    pontuacao.percentual = percentual.quantize(Decimal('0.01'))
    pontuacao.faixa = faixa_por_score(pontuacao.percentual)
    pontuacao.confiancas_previstas = CONFIANCAS_POR_MES if pontuacao.faixa in FAIXAS_PREMIADAS else 0


def _resumo(pontuacao):
    return {'total': float(pontuacao.total), 'percentual': float(pontuacao.percentual),
            'faixa': pontuacao.faixa}


def _conferir_ciclo(pontuacao):
    ciclo = pontuacao.mes.ciclo
    if ciclo.confiancas_creditadas or ciclo.status == ciclo.Status.ENCERRADO:
        raise AjusteRecusado('O ciclo já foi encerrado e as C$ já foram creditadas: '
                             'a pontuação dele não muda mais.')
    if not pontuacao.mes.is_fechado:
        raise AjusteRecusado('O mês ainda está aberto: o ajuste é feito depois de finalizar o mês.')


@transaction.atomic
def ajustar_pontuacao(pontuacao, valores, motivo, usuario):
    """Grava os pontos digitados pelo SUPERADMIN. `valores`: {campo: texto/número}.

    Cada item fica entre 0 e o peso dele para a pessoa. Devolve a pontuação salva.
    """
    pontuacao = PontuacaoMensal.objects.select_for_update().select_related('mes__ciclo', 'user').get(
        pk=pontuacao.pk)
    _conferir_ciclo(pontuacao)
    motivo = ' '.join((motivo or '').split())
    if not motivo:
        raise AjusteRecusado('Escreva o motivo do ajuste.')

    maximos = maximos_do_item(pontuacao.user)
    novos = {}
    for campo, _, rotulo in CAMPOS_PONTOS:
        bruto = valores.get(campo)
        if bruto in (None, ''):
            novos[campo] = Decimal(getattr(pontuacao, campo))
            continue
        try:
            valor = Decimal(str(bruto).strip().replace(',', '.'))
        except Exception:                                       # noqa: BLE001
            raise AjusteRecusado(f'{rotulo}: valor inválido.')
        if not valor.is_finite() or valor < 0 or valor > maximos[campo]:
            raise AjusteRecusado(f'{rotulo}: os pontos vão de 0 a {maximos[campo]:g}.')
        novos[campo] = valor.quantize(Decimal('0.01'))

    detalhes = dict(pontuacao.detalhes or {})
    ajuste = dict(detalhes.get('ajuste_manual') or {})
    antes = _resumo(pontuacao)
    if 'calculado' not in ajuste:
        # O que o fechamento calculou: é para onde "desfazer" volta.
        ajuste['calculado'] = {campo: float(getattr(pontuacao, campo)) for campo, _, _ in CAMPOS_PONTOS}
        ajuste['calculado_resumo'] = antes
    if all(novos[c] == Decimal(getattr(pontuacao, c)) for c, _, _ in CAMPOS_PONTOS):
        raise AjusteRecusado('Nenhum ponto foi alterado.')

    for campo, valor in novos.items():
        setattr(pontuacao, campo, valor)
    _recalcular(pontuacao)
    agora = timezone.localtime()
    registro = {'por': usuario.get_full_name() or usuario.get_username(), 'por_id': usuario.pk,
                'em': agora.strftime('%d/%m/%Y %H:%M'), 'motivo': motivo[:500],
                'antes': antes, 'depois': _resumo(pontuacao)}
    ajuste.update({'valores': {c: float(v) for c, v in novos.items()}, **registro})
    ajuste['historico'] = (ajuste.get('historico') or [])[-19:] + [registro]
    detalhes['ajuste_manual'] = ajuste
    pontuacao.detalhes = detalhes
    pontuacao.save()
    return pontuacao


@transaction.atomic
def desfazer_ajuste(pontuacao, usuario):
    """Volta para os pontos que o fechamento calculou."""
    pontuacao = PontuacaoMensal.objects.select_for_update().select_related('mes__ciclo', 'user').get(
        pk=pontuacao.pk)
    _conferir_ciclo(pontuacao)
    detalhes = dict(pontuacao.detalhes or {})
    ajuste = detalhes.get('ajuste_manual') or {}
    calculado = ajuste.get('calculado')
    if not calculado:
        raise AjusteRecusado('Esta pontuação não tem ajuste manual.')
    for campo, _, _ in CAMPOS_PONTOS:
        setattr(pontuacao, campo, Decimal(str(calculado.get(campo, 0))))
    _recalcular(pontuacao)
    historico = (detalhes.get('ajustes_desfeitos') or [])[-19:]
    historico.append({**{k: ajuste.get(k) for k in ('por', 'em', 'motivo', 'antes', 'depois')},
                      'desfeito_por': usuario.get_full_name() or usuario.get_username(),
                      'desfeito_em': timezone.localtime().strftime('%d/%m/%Y %H:%M')})
    detalhes['ajustes_desfeitos'] = historico
    detalhes.pop('ajuste_manual', None)
    pontuacao.detalhes = detalhes
    pontuacao.save()
    return pontuacao


@transaction.atomic
def fechar_mes(mes, usuario):
    """Congela a pontuação de todos os colaboradores no mês."""
    inicio, fim = periodo_do_mes_obj(mes)
    total_pessoas = 0
    # Ajuste manual feito antes de reabrir o mês: o fechamento de novo não pode apagá-lo.
    ajustes = {p.user_id: p.detalhes['ajuste_manual']
               for p in PontuacaoMensal.objects.filter(mes=mes).only('user_id', 'detalhes')
               if (p.detalhes or {}).get('ajuste_manual')}

    for colaborador in get_colaboradores():
        dados = calcular_pontuacao(colaborador, inicio=inicio, fim=fim)
        faixa = dados['faixa']
        premio = CONFIANCAS_POR_MES if faixa in FAIXAS_PREMIADAS else 0

        pontuacao, _ = PontuacaoMensal.objects.update_or_create(
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
                'detalhes': _para_json(dados['detalhes']),
            },
        )
        ajuste = ajustes.get(colaborador.pk)
        if ajuste and ajuste.get('valores'):
            # O calculado passa a ser o do fechamento novo; os pontos digitados continuam.
            ajuste = {**ajuste, 'calculado': {c: float(getattr(pontuacao, c)) for c, _, _ in CAMPOS_PONTOS},
                      'calculado_resumo': _resumo(pontuacao)}
            for campo, _, _ in CAMPOS_PONTOS:
                if campo in ajuste['valores']:
                    setattr(pontuacao, campo, Decimal(str(ajuste['valores'][campo])))
            _recalcular(pontuacao)
            pontuacao.detalhes = {**(pontuacao.detalhes or {}), 'ajuste_manual': ajuste}
            pontuacao.save()
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
    """Setor Destaque: a média das notas por setor principal, da maior para a menor.

    É a **média**, não a soma: somando, o setor com mais gente ganhava sempre —
    dez pessoas medianas passavam à frente de três excelentes, e o destaque
    deixava de dizer qualquer coisa sobre desempenho.

    No empate decide a soma (mais gente no mesmo nível pesa mais) e, por fim, o
    nome, para a ordem não dançar de um carregamento para o outro.
    """
    linhas = (PontuacaoMensal.objects.filter(mes=mes)
              .values('setor__id', 'setor__name')
              .annotate(soma=Sum('total'), media=Avg('percentual'), pessoas=Count('id'))
              .order_by('-media', '-soma', 'setor__name'))
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
