"""Motor de pontuação do Impulso — 100 pontos por mês.

CONFIAR (40)
    20  atividades/metas ..... 10 pela média das notas do gestor (qualidade+prazo)
                               10 pelo percentual de metas concluídas
    10  feedback ............. primeiro feedback, nota que subiu, ou >= 90
                               (nota do módulo /feedback, escala 0-100)
    10  assiduidade .......... folha de ponto: -2,5 por semana com problema

CONECTAR (40)
    10  curso do mês
    10  vídeos e POPs
    20  projeto foco

INOVAR (20)
    10  propor no mínimo 3 ideias
    10  ter 1 ideia aprovada

Faixas: Impulso 100% · Ouro >90% · Prata >70% · Bronze 0-70%.

O total possível é 100 para todo mundo, sempre. Um item que não existiu no mês
(nenhum curso publicado, nenhuma meta com prazo, sem folha de ponto) vale zero
e continua ocupando o lugar dele no total — não sai da conta. Antes ele saía, e
o resultado era que cada pessoa era medida numa régua diferente: quem tinha
poucos itens no mês era avaliado sobre 30 pontos e quem tinha muitos sobre 80,
então 11 pontos e 26,7 pontos viravam o mesmo percentual. Comparar duas pessoas
assim não significava nada.

A contrapartida é que ninguém ganha ponto por item que não teve — por isso
`pontos_sem_oportunidade` sai junto no cálculo, para as telas conseguirem
dizer quantos pontos ficaram de fora por falta de item publicado, em vez de
deixar parecer falha do colaborador.
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .assiduidade import nota_assiduidade
from .models import (ConclusaoConteudo, ConteudoConectar, Ideia, Meta,
                     ProjetoFoco, TarefaProjeto)
from .utils import faixa_por_score

# --- Pesos de cada item -----------------------------------------------------
# Os valores vivem no banco (PesosImpulso), editáveis pelo SUPERADMIN em
# /impulso/acompanhamento/. Estes aqui são só o ponto de partida: enquanto
# ninguém mexer, a régua é exatamente a que sempre foi.
PADRAO = {
    'metas_qualidade': Decimal('10'),
    'metas_conclusao': Decimal('10'),
    'feedback': Decimal('10'),
    'assiduidade': Decimal('10'),
    'curso': Decimal('10'),
    'videos_pops': Decimal('10'),
    'projeto_foco': Decimal('20'),
    'ideias': Decimal('10'),
    'ideia_aprovada': Decimal('10'),
}

PILAR_DO_ITEM = {
    'metas_qualidade': 'CONFIAR', 'metas_conclusao': 'CONFIAR',
    'feedback': 'CONFIAR', 'assiduidade': 'CONFIAR',
    'curso': 'CONECTAR', 'videos_pops': 'CONECTAR', 'projeto_foco': 'CONECTAR',
    'ideias': 'INOVAR', 'ideia_aprovada': 'INOVAR',
}


CHAVE_CACHE = 'impulso_pesos_v1'


def pesos(user=None):
    """Os pesos em vigor para esta pessoa (ou a régua geral).

    Zerar em caso de falha faria todo mundo tirar 0 no mês — o erro mais caro
    possível numa régua de premiação.

    Fica em cache curto porque o cálculo de uma pontuação consulta os pesos
    dezenas de vezes; sem isso seria uma consulta ao banco por item, por
    pessoa. A tela de edição limpa o cache ao salvar, então a régua nova vale
    na hora.
    """
    from django.core.cache import caches

    try:
        cache = caches['local']
    except Exception:                                        # noqa: BLE001
        cache = None

    chave = f'{CHAVE_CACHE}:{getattr(user, "pk", 0) or 0}'
    if cache is not None:
        guardado = cache.get(chave)
        if guardado:
            return {k: Decimal(v) for k, v in guardado.items()}

    try:
        from .models import PesosImpulso

        config = PesosImpulso.para(user)
        tabela = {campo: config.peso(campo) for campo in PADRAO}
    except Exception:                                        # noqa: BLE001
        return dict(PADRAO)

    if cache is not None:
        cache.set(chave, {k: str(v) for k, v in tabela.items()}, 60)
    return tabela


def limpar_cache_pesos(user=None):
    """Chamado ao salvar a régua: a mudança precisa valer na hora.

    Sem `user`, limpa tudo — é o caso de mexer na régua geral, que muda a nota
    de quem não tem régua própria.
    """
    from django.core.cache import caches
    try:
        cache = caches['local']
    except Exception:                                        # noqa: BLE001
        return
    if user is not None and getattr(user, 'pk', None):
        cache.delete(f'{CHAVE_CACHE}:{user.pk}')
        return
    # A régua geral alcança todo mundo: não dá para saber quem estava em cache.
    try:
        cache.clear()
    except Exception:                                        # noqa: BLE001
        cache.delete(f'{CHAVE_CACHE}:0')


def pt(campo, user=None, tabela=None):
    """Pontos de um item para esta pessoa. `tabela` evita reler a cada chamada."""
    return (tabela or pesos(user)).get(campo, PADRAO.get(campo, ZERO_INICIAL))


ZERO_INICIAL = Decimal('0')


def maximos(user=None, tabela=None):
    """(confiar, conectar, inovar, total) com os pesos desta pessoa."""
    tabela = tabela or pesos(user)
    por_pilar = {'CONFIAR': Decimal('0'), 'CONECTAR': Decimal('0'),
                 'INOVAR': Decimal('0')}
    for campo, valor in tabela.items():
        por_pilar[PILAR_DO_ITEM[campo]] += valor
    total = sum(por_pilar.values(), Decimal('0'))
    return por_pilar['CONFIAR'], por_pilar['CONECTAR'], por_pilar['INOVAR'], total

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
    # Só meta aprovada conta. Uma solicitação parada na fila do gestor (ou
    # recusada) não pode entrar no denominador e derrubar a nota de quem
    # simplesmente pediu uma tarefa.
    metas = Meta.objects.filter(colaborador=user, prazo__gte=inicio, prazo__lte=fim,
                                aprovacao=Meta.Aprovacao.APROVADA)
    total = metas.count()
    if not total:
        return ZERO, ZERO, ZERO, ZERO, {'sem_metas': True}

    concluidas = metas.filter(status=Meta.Status.CONCLUIDA).count()
    avaliadas = list(metas.filter(nota_qualidade__isnull=False, nota_prazo__isnull=False))

    # Conclusão (0-10)
    p_conclusao = pt('metas_conclusao', user) * Decimal(concluidas) / Decimal(total)

    # Qualidade (0-10): média de (qualidade + prazo) numa escala 0-10 por meta
    if avaliadas:
        soma_q = sum(m.nota_qualidade for m in avaliadas)
        soma_p = sum(m.nota_prazo for m in avaliadas)
        n = len(avaliadas)
        media_q = Decimal(soma_q) / Decimal(n)
        media_p = Decimal(soma_p) / Decimal(n)
        p_qualidade = pt('metas_qualidade', user) * (media_q + media_p) / Decimal('10')
        aplicavel_qualidade = pt('metas_qualidade', user)
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
            _quantize(p_conclusao), pt('metas_conclusao', user), detalhes)


def _nota_anterior(fb):
    """Nota (0-100) do feedback anterior desta pessoa, se houver alguma com nota.

    Pula os feedbacks antigos que ficaram sem nota preenchida: comparar com um
    formulário em branco não diz nada, e tratar isso como "piorou" castigaria a
    pessoa por uma falha de preenchimento de outro.
    """
    from feedback.models import Feedback

    anteriores = (Feedback.objects
                  .filter(evaluatee_id=fb.evaluatee_id, created_at__lt=fb.created_at)
                  .order_by('-created_at')[:20])
    for antigo in anteriores:
        try:
            media = antigo.average_score()
        except Exception:
            media = None
        if media is not None:
            return round(float(media) * 10, 1)
    return None


def avaliar_feedback(fb):
    """Este feedback garante os 10 pontos do Impulso? E por qual caminho?

    São três caminhos, e basta um:

    1. **Primeiro feedback** que a pessoa recebe do gestor — ninguém começa
       devendo ponto por não ter histórico.
    2. **Evoluiu**: a nota subiu em relação ao feedback anterior.
    3. **Nota alta**: 90 de 100 (ou 9 de 10) para cima.

    Quem já está no topo não perde ponto por não ter como subir, e quem começou
    embaixo ganha ponto ao melhorar — é o que a regra dos três caminhos resolve.
    """
    try:
        media = fb.average_score()
    except Exception:
        media = None
    if media is None:
        return None

    nota = round(float(media) * 10, 1)                 # escala 0-10 -> 0-100
    anterior = _nota_anterior(fb)

    primeiro = anterior is None
    evoluiu = (anterior is not None) and nota > anterior
    nota_alta = nota >= FEEDBACK_NOTA_MINIMA
    atingiu = primeiro or evoluiu or nota_alta

    if nota_alta:
        motivo = f'nota {nota:g} — acima de {FEEDBACK_NOTA_MINIMA}'
    elif primeiro:
        motivo = 'primeiro feedback recebido'
    elif evoluiu:
        motivo = f'evoluiu de {anterior:g} para {nota:g}'
    else:
        motivo = f'nota {nota:g} — não subiu ({anterior:g}) nem chegou a {FEEDBACK_NOTA_MINIMA}'

    return {
        'nota': nota, 'media': media, 'anterior': anterior,
        'primeiro': primeiro, 'evoluiu': evoluiu, 'nota_alta': nota_alta,
        'atingiu': atingiu, 'motivo': motivo,
        'minimo': FEEDBACK_NOTA_MINIMA,
    }


def _nota_feedback(user, inicio, fim):
    """10 pontos se algum feedback do mês fechar por um dos três caminhos."""
    try:
        from feedback.models import Feedback
    except Exception:
        return ZERO, ZERO, {'indisponivel': True}

    feedbacks = (Feedback.objects
                 .filter(evaluatee=user, data__gte=inicio, data__lte=fim)
                 .order_by('-data', '-created_at'))

    escolhido = None
    for fb in feedbacks:
        dados = avaliar_feedback(fb)
        if dados is None:
            continue
        # Vale o que garante o ponto; entre dois que garantem, a nota maior.
        melhor_que = (
            escolhido is None
            or (dados['atingiu'] and not escolhido['atingiu'])
            or (dados['atingiu'] == escolhido['atingiu']
                and dados['nota'] > escolhido['nota'])
        )
        if melhor_que:
            escolhido = dados

    if escolhido is None:
        return ZERO, ZERO, {'sem_feedback': True}

    return (pt('feedback', user) if escolhido['atingiu'] else ZERO), pt('feedback', user), escolhido


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
    feitas = ConclusaoConteudo.objects.filter(
        user=user, conteudo_id__in=ids, concluido=True)
    # Só pontua o que o gestor conferiu — mesma régua do Confiar, onde a
    # entrega passa pela avaliação. "Marquei como feito" não é "está feito".
    concluidos = feitas.filter(
        aprovacao=ConclusaoConteudo.Aprovacao.APROVADA).count()
    aguardando = feitas.filter(
        aprovacao=ConclusaoConteudo.Aprovacao.PENDENTE).count()
    recusados = feitas.filter(
        aprovacao=ConclusaoConteudo.Aprovacao.RECUSADA).count()
    nota = pontos * Decimal(concluidos) / Decimal(total)
    return _quantize(nota), pontos, {
        'total': total, 'concluidos': concluidos,
        # A tela precisa distinguir "ainda não fiz" de "fiz e espera
        # conferência": o segundo não é culpa de quem fez.
        'aguardando': aguardando, 'recusados': recusados,
    }


def _nota_projeto_foco(user, inicio, fim):
    """Os pontos do Projeto FOCO, em duas metades.

    Metade pela entrega da parte da pessoa; a outra metade quando o projeto é
    concluído. A régua anterior pagava tudo por tarefa concluída — quem
    entregava a sua parte pontuava igual em projeto que saiu e em projeto que
    morreu no meio, e ninguém tinha motivo para empurrar o conjunto.

    As duas metades são proporcionais: 3 de 4 tarefas feitas valem 3/4 da
    primeira; estar em 2 projetos com 1 concluído vale metade da segunda.
    """
    tarefas = TarefaProjeto.objects.filter(
        responsavel=user, projeto__ativo=True).filter(
        Q(prazo__isnull=True) | Q(prazo__gte=inicio, prazo__lte=fim))
    total = tarefas.count()
    if not total:
        return ZERO, ZERO, {'sem_tarefas': True}

    maximo = pt('projeto_foco', user)
    metade = maximo / Decimal(2)

    concluidas = tarefas.filter(status=TarefaProjeto.Status.CONCLUIDA).count()
    nota_entrega = metade * Decimal(concluidas) / Decimal(total)

    ids = set(tarefas.values_list('projeto_id', flat=True))
    entregues = ProjetoFoco.objects.filter(id__in=ids, concluido=True).count()
    nota_conclusao = metade * Decimal(entregues) / Decimal(len(ids)) if ids else ZERO

    return _quantize(nota_entrega + nota_conclusao), maximo, {
        'total': total, 'concluidas': concluidas,
        'projetos': len(ids), 'projetos_concluidos': entregues,
        'metade': _quantize(metade),
        'pontos_entrega': _quantize(nota_entrega),
        'pontos_conclusao': _quantize(nota_conclusao),
    }


# ---------------------------------------------------------------------------
# INOVAR
# ---------------------------------------------------------------------------
def _nota_inovar(user, inicio, fim):
    # Conta a ideia para quem escreveu e para quem foi incluído nela: sem o
    # `distinct`, uma pessoa que é autora E participante contaria duas vezes.
    ideias = Ideia.objects.filter(
        Q(autor=user) | Q(participantes=user),
        criado_em__date__gte=inicio, criado_em__date__lte=fim,
    ).distinct()
    propostas = ideias.count()
    aprovadas = ideias.filter(status=Ideia.Status.APROVADA).count()

    p_ideias = pt('ideias', user) if propostas >= IDEIAS_MINIMAS else ZERO
    p_aprovada = pt('ideia_aprovada', user) if aprovadas >= 1 else ZERO
    detalhes = {
        'propostas': propostas, 'minimo': IDEIAS_MINIMAS, 'aprovadas': aprovadas,
    }
    return p_ideias, pt('ideias', user), p_aprovada, pt('ideia_aprovada', user), detalhes


def _info_projeto_foco(detalhe):
    """Explica as duas metades, porque a nota já não sai só das tarefas."""
    if detalhe.get('sem_tarefas'):
        return 'Sem tarefas de projeto foco'
    return ('%s de %s tarefa(s) concluída(s) (%s pt) · %s de %s projeto(s) '
            'concluído(s) (%s pt)' % (
                detalhe.get('concluidas', 0), detalhe.get('total', 0),
                detalhe.get('pontos_entrega', 0),
                detalhe.get('projetos_concluidos', 0), detalhe.get('projetos', 0),
                detalhe.get('pontos_conclusao', 0)))


def _info_assiduidade(detalhe):
    """Uma linha explicando de onde saiu a nota de assiduidade."""
    if detalhe.get('fonte') == 'ponto':
        if detalhe.get('sem_dias_avaliaveis'):
            return 'Sem dias de trabalho no mês'
        return detalhe.get('motivo') or (
            '%s marcação(ões) esquecida(s) de %s dia(s) úteis' % (
                detalhe.get('total_falhas', 0), detalhe.get('dias_uteis', 0)))
    if detalhe.get('sem_folha'):
        return 'Ponto não sincronizado e folha não importada'
    return '%s de %s semana(s) com problema' % (
        detalhe.get('semanas_com_problema', 0), detalhe.get('semanas_avaliadas', 0))


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
        user, [ConteudoConectar.Tipo.CURSO], pt('curso', user), inicio, fim)
    p_vp, ap_vp, det_vp = _nota_conteudos(
        user, [ConteudoConectar.Tipo.VIDEO, ConteudoConectar.Tipo.POP],
        pt('videos_pops', user), inicio, fim)
    p_proj, ap_proj, det_proj = _nota_projeto_foco(user, inicio, fim)

    (p_ideias, ap_ideias, p_aprov, ap_aprov, det_inovar) = _nota_inovar(user, inicio, fim)

    confiar = p_metas_q + p_metas_c + p_feedback + p_assid
    conectar = p_curso + p_vp + p_proj
    inovar = p_ideias + p_aprov
    total = confiar + conectar + inovar

    # Os `ap_*` que cada função devolve dizem se o item existiu no mês. Não
    # servem mais de denominador — servem para explicar de onde vem um zero que
    # não é culpa de ninguém.
    houve = (ap_metas_q + ap_metas_c + ap_feedback + ap_assid
             + ap_curso + ap_vp + ap_proj + ap_ideias + ap_aprov)
    # A régua é a desta pessoa: quem tem pesos próprios não pode ser medido
    # contra o máximo de outro.
    max_confiar, max_conectar, max_inovar, max_total = maximos(user)
    sem_oportunidade = max_total - houve

    aplicavel = max_total
    percentual = total / max_total * 100 if max_total else Decimal('0')

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
        'confiar': _quantize(confiar), 'confiar_max': _quantize(max_confiar),
        'conectar': _quantize(conectar), 'conectar_max': _quantize(max_conectar),
        'inovar': _quantize(inovar), 'inovar_max': _quantize(max_inovar),
        # Geral
        'total': _quantize(total),
        'aplicavel': _quantize(aplicavel),
        'pontos_sem_oportunidade': _quantize(sem_oportunidade),
        'percentual': _quantize(percentual),
        'faixa': faixa_por_score(percentual),
        # Vai junto para o linhas_detalhadas desenhar o "de quanto" certo:
        # ele não recebe a pessoa, e reler o banco lá daria a régua errada.
        'pesos': pesos(user),
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
    # A régua vem junto do cálculo: esta função não recebe a pessoa, e reler o
    # banco aqui devolveria a régua geral para quem tem a própria.
    tabela = dados.get('pesos') or pesos()
    return [
        {'bloco': 'CONFIAR', 'item': 'Metas — qualidade das entregas',
         'pontos': dados['p_metas_qualidade'], 'max': pt('metas_qualidade', tabela=tabela),
         'info': ('%s meta(s) avaliada(s) · qualidade %s/5 · prazo %s/5' % (
             d['metas'].get('avaliadas', 0), d['metas'].get('media_qualidade') or '—',
             d['metas'].get('media_prazo') or '—'))
         if not d['metas'].get('sem_metas') else 'Nenhuma meta com prazo no mês'},
        {'bloco': 'CONFIAR', 'item': 'Metas — conclusão',
         'pontos': dados['p_metas_conclusao'], 'max': pt('metas_conclusao', tabela=tabela),
         'info': ('%s de %s concluída(s)' % (
             d['metas'].get('concluidas', 0), d['metas'].get('total', 0)))
         if not d['metas'].get('sem_metas') else 'Nenhuma meta com prazo no mês'},
        {'bloco': 'CONFIAR', 'item': 'Feedback do gestor',
         'pontos': dados['p_feedback'], 'max': pt('feedback', tabela=tabela),
         'info': (d['feedback'].get('motivo') or '—')
         if not d['feedback'].get('sem_feedback') else 'Sem feedback no mês'},
        {'bloco': 'CONFIAR',
         'item': ('Assiduidade (ponto eletrônico)'
                  if d['assiduidade'].get('fonte') == 'ponto'
                  else 'Assiduidade (folha de ponto)'),
         'pontos': dados['p_assiduidade'], 'max': pt('assiduidade', tabela=tabela),
         'info': _info_assiduidade(d['assiduidade'])},
        {'bloco': 'CONECTAR', 'item': 'Curso do mês',
         'pontos': dados['p_curso'], 'max': pt('curso', tabela=tabela),
         'info': ('%s de %s concluído(s)' % (
             d['curso'].get('concluidos', 0), d['curso'].get('total', 0)))
         if not d['curso'].get('sem_conteudo') else 'Nenhum curso obrigatório no mês'},
        {'bloco': 'CONECTAR', 'item': 'Vídeos e POPs',
         'pontos': dados['p_videos_pops'], 'max': pt('videos_pops', tabela=tabela),
         'info': ('%s de %s concluído(s)' % (
             d['videos_pops'].get('concluidos', 0), d['videos_pops'].get('total', 0)))
         if not d['videos_pops'].get('sem_conteudo') else 'Nenhum vídeo/POP obrigatório'},
        {'bloco': 'CONECTAR', 'item': 'Projeto foco',
         'pontos': dados['p_projeto_foco'], 'max': pt('projeto_foco', tabela=tabela),
         'info': _info_projeto_foco(d['projeto_foco'])},
        {'bloco': 'INOVAR', 'item': 'Propor %s ideias' % IDEIAS_MINIMAS,
         'pontos': dados['p_ideias'], 'max': pt('ideias', tabela=tabela),
         'info': '%s ideia(s) proposta(s)' % d['inovar'].get('propostas', 0)},
        {'bloco': 'INOVAR', 'item': 'Ideia aprovada',
         'pontos': dados['p_ideia_aprovada'], 'max': pt('ideia_aprovada', tabela=tabela),
         'info': '%s aprovada(s)' % d['inovar'].get('aprovadas', 0)},
    ]
