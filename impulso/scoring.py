"""Motor de pontuação do Impulso — 100 pontos por mês.

CONFIAR (40)
    20  atividades/metas ..... 10 pela média das notas do gestor (qualidade+prazo)
                               10 pelo percentual de metas concluídas
    10  feedback ............. nota do módulo /feedback >= 90 (escala 0-100)
    10  assiduidade .......... folha de ponto: -2,5 por semana com problema

CONECTAR (40)
    10  curso do mês
    10  vídeos e POPs
    20  projeto foco

INOVAR (20)
    10  propor no mínimo 3 ideias
    10  ter 1 ideia aprovada

Faixas: Impulso 100% · Ouro >90% · Prata >70% · Bronze 0-70%.

Nota sobre "pontos aplicáveis": se um item não existe no mês (ex.: nenhum
curso foi publicado, ou não há folha de ponto), ele sai do total possível em
vez de zerar a nota do colaborador. O percentual — que define a faixa — é
sempre obtidos/aplicáveis.
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .assiduidade import nota_assiduidade
from .models import (ConclusaoConteudo, ConteudoConectar, Ideia, Meta,
                     TarefaProjeto)
from .utils import faixa_por_score

# --- Pesos de cada item -----------------------------------------------------
PT_METAS_QUALIDADE = Decimal('10')
PT_METAS_CONCLUSAO = Decimal('10')
PT_FEEDBACK = Decimal('10')
PT_ASSIDUIDADE = Decimal('10')
PT_CURSO = Decimal('10')
PT_VIDEOS_POPS = Decimal('10')
PT_PROJETO_FOCO = Decimal('20')
PT_IDEIAS = Decimal('10')
PT_IDEIA_APROVADA = Decimal('10')

FEEDBACK_NOTA_MINIMA = 90   # escala 0-100
IDEIAS_MINIMAS = 3

ZERO = Decimal('0')


def _quantize(valor):
    return Decimal(valor).quantize(Decimal('0.01'))


def periodo_do_mes(referencia=None):
    """(primeiro_dia, ultimo_dia) do mês de `referencia` (padrão: hoje)."""
    hoje = referencia or timezone.localdate()
    inicio = date(hoje.year, hoje.month, 1)
    fim = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])
    return inicio, fim


# ---------------------------------------------------------------------------
# CONFIAR
# ---------------------------------------------------------------------------
def _nota_metas(user, inicio, fim):
    """20 pontos: 10 pela qualidade média + 10 pelo percentual de conclusão."""
    metas = Meta.objects.filter(colaborador=user, prazo__gte=inicio, prazo__lte=fim)
    total = metas.count()
    if not total:
        return ZERO, ZERO, ZERO, ZERO, {'sem_metas': True}

    concluidas = metas.filter(status=Meta.Status.CONCLUIDA).count()
    avaliadas = list(metas.filter(nota_qualidade__isnull=False, nota_prazo__isnull=False))

    # Conclusão (0-10)
    p_conclusao = PT_METAS_CONCLUSAO * Decimal(concluidas) / Decimal(total)

    # Qualidade (0-10): média de (qualidade + prazo) numa escala 0-10 por meta
    if avaliadas:
        soma_q = sum(m.nota_qualidade for m in avaliadas)
        soma_p = sum(m.nota_prazo for m in avaliadas)
        n = len(avaliadas)
        media_q = Decimal(soma_q) / Decimal(n)
        media_p = Decimal(soma_p) / Decimal(n)
        p_qualidade = PT_METAS_QUALIDADE * (media_q + media_p) / Decimal('10')
        aplicavel_qualidade = PT_METAS_QUALIDADE
    else:
        media_q = media_p = None
        p_qualidade = ZERO
        aplicavel_qualidade = ZERO  # nenhuma meta avaliada ainda

    detalhes = {
        'total': total,
        'concluidas': concluidas,
        'avaliadas': len(avaliadas),
        'media_qualidade': float(round(media_q, 2)) if media_q is not None else None,
        'media_prazo': float(round(media_p, 2)) if media_p is not None else None,
    }
    return (_quantize(p_qualidade), aplicavel_qualidade,
            _quantize(p_conclusao), PT_METAS_CONCLUSAO, detalhes)


def _nota_feedback(user, inicio, fim):
    """10 pontos se o feedback do mês (0-100) atingir a nota mínima."""
    try:
        from feedback.models import Feedback
    except Exception:
        return ZERO, ZERO, {'indisponivel': True}

    feedbacks = Feedback.objects.filter(
        evaluatee=user, data__gte=inicio, data__lte=fim).order_by('-data')

    melhor = None
    for fb in feedbacks:
        try:
            media = fb.average_score()
        except Exception:
            media = None
        if media is None:
            continue
        nota = float(media) * 10  # 0-10 -> 0-100
        if melhor is None or nota > melhor:
            melhor = nota

    if melhor is None:
        return ZERO, ZERO, {'sem_feedback': True}

    atingiu = melhor >= FEEDBACK_NOTA_MINIMA
    detalhes = {'nota': round(melhor, 1), 'minimo': FEEDBACK_NOTA_MINIMA, 'atingiu': atingiu}
    return (PT_FEEDBACK if atingiu else ZERO), PT_FEEDBACK, detalhes


# ---------------------------------------------------------------------------
# CONECTAR
# ---------------------------------------------------------------------------
def _conteudos_do_usuario(user, tipos, inicio, fim):
    """Conteúdos obrigatórios aplicáveis ao usuário no período."""
    return (ConteudoConectar.objects
            .filter(ativo=True, obrigatorio=True, tipo__in=tipos)
            .filter(criado_em__date__lte=fim)
            .filter(Q(fim__isnull=True) | Q(fim__gte=inicio))
            .filter(Q(obrigatorio_para__isnull=True) | Q(obrigatorio_para=user))
            .distinct())


def _nota_conteudos(user, tipos, pontos, inicio, fim):
    conteudos = list(_conteudos_do_usuario(user, tipos, inicio, fim))
    total = len(conteudos)
    if not total:
        return ZERO, ZERO, {'sem_conteudo': True}

    ids = [c.id for c in conteudos]
    concluidos = ConclusaoConteudo.objects.filter(
        user=user, conteudo_id__in=ids, concluido=True).count()
    nota = pontos * Decimal(concluidos) / Decimal(total)
    return _quantize(nota), pontos, {'total': total, 'concluidos': concluidos}


def _nota_projeto_foco(user, inicio, fim):
    """20 pontos pelo andamento das tarefas do usuário em projetos foco."""
    tarefas = TarefaProjeto.objects.filter(
        responsavel=user, projeto__ativo=True).filter(
        Q(prazo__isnull=True) | Q(prazo__gte=inicio, prazo__lte=fim))
    total = tarefas.count()
    if not total:
        return ZERO, ZERO, {'sem_tarefas': True}
    concluidas = tarefas.filter(status=TarefaProjeto.Status.CONCLUIDA).count()
    nota = PT_PROJETO_FOCO * Decimal(concluidas) / Decimal(total)
    return _quantize(nota), PT_PROJETO_FOCO, {'total': total, 'concluidas': concluidas}


# ---------------------------------------------------------------------------
# INOVAR
# ---------------------------------------------------------------------------
def _nota_inovar(user, inicio, fim):
    ideias = Ideia.objects.filter(
        autor=user, criado_em__date__gte=inicio, criado_em__date__lte=fim)
    propostas = ideias.count()
    aprovadas = ideias.filter(status=Ideia.Status.APROVADA).count()

    p_ideias = PT_IDEIAS if propostas >= IDEIAS_MINIMAS else ZERO
    p_aprovada = PT_IDEIA_APROVADA if aprovadas >= 1 else ZERO
    detalhes = {
        'propostas': propostas, 'minimo': IDEIAS_MINIMAS, 'aprovadas': aprovadas,
    }
    return p_ideias, PT_IDEIAS, p_aprovada, PT_IDEIA_APROVADA, detalhes


# ---------------------------------------------------------------------------
# Cálculo completo
# ---------------------------------------------------------------------------
def calcular_pontuacao(user, inicio=None, fim=None, referencia=None):
    """Pontuação completa do colaborador no período (padrão: mês corrente)."""
    if inicio is None or fim is None:
        inicio, fim = periodo_do_mes(referencia)

    (p_metas_q, ap_metas_q, p_metas_c, ap_metas_c, det_metas) = _nota_metas(user, inicio, fim)
    p_feedback, ap_feedback, det_feedback = _nota_feedback(user, inicio, fim)
    p_assid, ap_assid, det_assid = nota_assiduidade(user, inicio.year, inicio.month)

    p_curso, ap_curso, det_curso = _nota_conteudos(
        user, [ConteudoConectar.Tipo.CURSO], PT_CURSO, inicio, fim)
    p_vp, ap_vp, det_vp = _nota_conteudos(
        user, [ConteudoConectar.Tipo.VIDEO, ConteudoConectar.Tipo.POP],
        PT_VIDEOS_POPS, inicio, fim)
    p_proj, ap_proj, det_proj = _nota_projeto_foco(user, inicio, fim)

    (p_ideias, ap_ideias, p_aprov, ap_aprov, det_inovar) = _nota_inovar(user, inicio, fim)

    confiar = p_metas_q + p_metas_c + p_feedback + p_assid
    conectar = p_curso + p_vp + p_proj
    inovar = p_ideias + p_aprov
    total = confiar + conectar + inovar

    aplicavel = (ap_metas_q + ap_metas_c + ap_feedback + ap_assid
                 + ap_curso + ap_vp + ap_proj + ap_ideias + ap_aprov)
    percentual = (total / aplicavel * 100) if aplicavel > 0 else ZERO

    return {
        'inicio': inicio, 'fim': fim,
        # CONFIAR
        'p_metas_qualidade': _quantize(p_metas_q),
        'p_metas_conclusao': _quantize(p_metas_c),
        'p_feedback': _quantize(p_feedback),
        'p_assiduidade': _quantize(p_assid),
        # CONECTAR
        'p_curso': _quantize(p_curso),
        'p_videos_pops': _quantize(p_vp),
        'p_projeto_foco': _quantize(p_proj),
        # INOVAR
        'p_ideias': _quantize(p_ideias),
        'p_ideia_aprovada': _quantize(p_aprov),
        # Totais por bloco
        'confiar': _quantize(confiar), 'confiar_max': _quantize(
            ap_metas_q + ap_metas_c + ap_feedback + ap_assid),
        'conectar': _quantize(conectar), 'conectar_max': _quantize(
            ap_curso + ap_vp + ap_proj),
        'inovar': _quantize(inovar), 'inovar_max': _quantize(ap_ideias + ap_aprov),
        # Geral
        'total': _quantize(total),
        'aplicavel': _quantize(aplicavel),
        'percentual': _quantize(percentual),
        'faixa': faixa_por_score(percentual),
        'detalhes': {
            'metas': det_metas, 'feedback': det_feedback, 'assiduidade': det_assid,
            'curso': det_curso, 'videos_pops': det_vp, 'projeto_foco': det_proj,
            'inovar': det_inovar,
        },
    }


# Cores dos blocos — paleta validada para daltonismo (ΔE 15.0):
# laranja / ciano / violeta. Sempre acompanhadas de rótulo e ícone.
COR_CONFIAR = '#EA580C'
COR_CONECTAR = '#0891B2'
COR_INOVAR = '#7C3AED'


def blocos_resumo(dados):
    """Os 3 blocos formatados para as barras de progresso."""
    def _pct(valor, maximo):
        return float(valor) / float(maximo) * 100 if maximo and float(maximo) > 0 else 0

    return [
        {'nome': 'CONFIAR', 'icone': 'fa-handshake', 'cor': COR_CONFIAR,
         'valor': dados['confiar'], 'max': dados['confiar_max'],
         'pct': round(_pct(dados['confiar'], dados['confiar_max']), 1)},
        {'nome': 'CONECTAR', 'icone': 'fa-graduation-cap', 'cor': COR_CONECTAR,
         'valor': dados['conectar'], 'max': dados['conectar_max'],
         'pct': round(_pct(dados['conectar'], dados['conectar_max']), 1)},
        {'nome': 'INOVAR', 'icone': 'fa-lightbulb', 'cor': COR_INOVAR,
         'valor': dados['inovar'], 'max': dados['inovar_max'],
         'pct': round(_pct(dados['inovar'], dados['inovar_max']), 1)},
    ]


def linhas_detalhadas(dados):
    """Formata o cálculo para as telas de detalhamento (uma linha por item)."""
    d = dados['detalhes']
    return [
        {'bloco': 'CONFIAR', 'item': 'Metas — qualidade das entregas',
         'pontos': dados['p_metas_qualidade'], 'max': PT_METAS_QUALIDADE,
         'info': ('%s meta(s) avaliada(s) · qualidade %s/5 · prazo %s/5' % (
             d['metas'].get('avaliadas', 0), d['metas'].get('media_qualidade') or '—',
             d['metas'].get('media_prazo') or '—'))
         if not d['metas'].get('sem_metas') else 'Nenhuma meta com prazo no mês'},
        {'bloco': 'CONFIAR', 'item': 'Metas — conclusão',
         'pontos': dados['p_metas_conclusao'], 'max': PT_METAS_CONCLUSAO,
         'info': ('%s de %s concluída(s)' % (
             d['metas'].get('concluidas', 0), d['metas'].get('total', 0)))
         if not d['metas'].get('sem_metas') else 'Nenhuma meta com prazo no mês'},
        {'bloco': 'CONFIAR', 'item': 'Feedback do gestor',
         'pontos': dados['p_feedback'], 'max': PT_FEEDBACK,
         'info': ('nota %s (mínimo %s)' % (
             d['feedback'].get('nota'), d['feedback'].get('minimo')))
         if not d['feedback'].get('sem_feedback') else 'Sem feedback no mês'},
        {'bloco': 'CONFIAR', 'item': 'Assiduidade (folha de ponto)',
         'pontos': dados['p_assiduidade'], 'max': PT_ASSIDUIDADE,
         'info': ('%s de %s semana(s) com problema' % (
             d['assiduidade'].get('semanas_com_problema', 0),
             d['assiduidade'].get('semanas_avaliadas', 0)))
         if not d['assiduidade'].get('sem_folha') else 'Folha de ponto não importada'},
        {'bloco': 'CONECTAR', 'item': 'Curso do mês',
         'pontos': dados['p_curso'], 'max': PT_CURSO,
         'info': ('%s de %s concluído(s)' % (
             d['curso'].get('concluidos', 0), d['curso'].get('total', 0)))
         if not d['curso'].get('sem_conteudo') else 'Nenhum curso obrigatório no mês'},
        {'bloco': 'CONECTAR', 'item': 'Vídeos e POPs',
         'pontos': dados['p_videos_pops'], 'max': PT_VIDEOS_POPS,
         'info': ('%s de %s concluído(s)' % (
             d['videos_pops'].get('concluidos', 0), d['videos_pops'].get('total', 0)))
         if not d['videos_pops'].get('sem_conteudo') else 'Nenhum vídeo/POP obrigatório'},
        {'bloco': 'CONECTAR', 'item': 'Projeto foco',
         'pontos': dados['p_projeto_foco'], 'max': PT_PROJETO_FOCO,
         'info': ('%s de %s tarefa(s) concluída(s)' % (
             d['projeto_foco'].get('concluidas', 0), d['projeto_foco'].get('total', 0)))
         if not d['projeto_foco'].get('sem_tarefas') else 'Sem tarefas de projeto foco'},
        {'bloco': 'INOVAR', 'item': 'Propor %s ideias' % IDEIAS_MINIMAS,
         'pontos': dados['p_ideias'], 'max': PT_IDEIAS,
         'info': '%s ideia(s) proposta(s)' % d['inovar'].get('propostas', 0)},
        {'bloco': 'INOVAR', 'item': 'Ideia aprovada',
         'pontos': dados['p_ideia_aprovada'], 'max': PT_IDEIA_APROVADA,
         'info': '%s aprovada(s)' % d['inovar'].get('aprovadas', 0)},
    ]
