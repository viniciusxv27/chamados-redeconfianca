"""A partida: fases, relógio, respostas, pontos e ranking.

Tudo pelo relógio do servidor. As telas (responsável e participantes) consultam o
estado a cada segundo e desenham o que ele disser — ninguém depende do relógio do
celular de ninguém, e ninguém vê a resposta certa antes da hora.

Fases (``Sala.Fase``): AGENDADA → PERGUNTA → RESULTADO → PERGUNTA → … → ENCERRADA.

- PERGUNTA: a pergunta está no ar. O tempo de cada pessoa conta de quando a
  pergunta chegou na tela dela (``Resposta.vista_em``): quem recebe um segundo
  depois não perde por isso. Acabado o tempo (com a folga da rede) ou quando
  todos os que entraram já responderam, a sala passa sozinha para RESULTADO.
- RESULTADO: a certa, quantos marcaram cada alternativa e o ranking. O
  responsável avança para a próxima (ou para o pódio, na última).
- Resposta: uma só por pergunta — confirmada, não muda.
- Pontos: só quem acerta. De 1000 (na hora) a 500 (no último instante), em linha.
- Ranking: pontos; empate → menos tempo somado nos acertos; depois, o nome.

Carga: cada tela consulta a sala a cada 1–2,5 s, e cada consulta ainda passa
pelos middlewares do portal. Por isso a consulta do participante é enxuta: as
perguntas de um quiz publicado (que não mudam mais) ficam na memória do
processo, o "todos já responderam?" só é conferido na resposta e na tela do
responsável, e o placar de cada passo (que não muda dentro dele) é montado uma
vez e servido a todos por alguns segundos.
"""
from datetime import timedelta

from django.core.cache import caches
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from .models import PONTOS_MAXIMOS, Participante, Pergunta, Resposta, Sala

# Resposta que sai no fim do tempo e chega logo depois ainda vale — e quem viu a
# pergunta alguns segundos depois (a tela consulta a cada ~2 s) não perde tempo de resposta.
FOLGA_DA_REDE = timedelta(seconds=3)
ONLINE_POR = timedelta(seconds=25)           # "está com a sala aberta": consultou há menos que isso
GRAVAR_VISTO_A_CADA = timedelta(seconds=10)  # o "visto por último" não precisa de uma escrita por segundo
TOP_DO_PLACAR = 5
PLACAR_NA_MEMORIA_POR = 5                   # segundos; o placar de um passo não muda dentro dele
_PERGUNTAS_PUBLICADAS = {}                  # (quiz_id, publicado_em) -> perguntas, na memória do processo


class ErroJogo(Exception):
    """O passo pedido não vale agora (fase errada, tempo acabado, já respondeu...)."""


def agora():
    return timezone.now()


# ---------------------------------------------------------------------------
# Perguntas da sala
# ---------------------------------------------------------------------------
def perguntas_da_sala(sala):
    """As perguntas do quiz na ordem, com as alternativas (guardadas na instância).

    Quiz publicado não muda as perguntas (e só volta a rascunho sem sala marcada
    nem jogada), então elas ficam na memória do processo pela data da publicação.
    """
    if getattr(sala, '_perguntas', None) is None:
        quiz = sala.quiz
        chave = (quiz.pk, quiz.publicado_em) if quiz.publicado and quiz.publicado_em else None
        perguntas = _PERGUNTAS_PUBLICADAS.get(chave) if chave else None
        if perguntas is None:
            perguntas = list(Pergunta.objects.filter(quiz_id=sala.quiz_id)
                             .prefetch_related('alternativas').order_by('ordem', 'id'))
            if chave:
                if len(_PERGUNTAS_PUBLICADAS) >= 50:
                    _PERGUNTAS_PUBLICADAS.clear()
                _PERGUNTAS_PUBLICADAS[chave] = perguntas
        sala._perguntas = perguntas
    return sala._perguntas


def pergunta_no_ar(sala):
    perguntas = perguntas_da_sala(sala)
    if sala.pergunta_atual is None or not 0 <= sala.pergunta_atual < len(perguntas):
        return None
    return perguntas[sala.pergunta_atual]


def fim_da_pergunta(sala, pergunta):
    return sala.pergunta_inicio + timedelta(seconds=pergunta.tempo_limite)


def pontos_por(correta, tempo_ms, limite_s):
    """1000 na hora, 500 no último instante; zero para quem erra."""
    if not correta:
        return 0
    fracao = min(max((tempo_ms or 0) / (limite_s * 1000.0), 0.0), 1.0)
    return round(PONTOS_MAXIMOS * (1 - fracao / 2))


# ---------------------------------------------------------------------------
# Passos do responsável
# ---------------------------------------------------------------------------
def _travar(sala):
    travada = Sala.objects.select_for_update().get(pk=sala.pk)
    travada._perguntas = getattr(sala, '_perguntas', None)
    return travada


def _mudar(sala, **campos):
    for campo, valor in campos.items():
        setattr(sala, campo, valor)
    sala.versao = (sala.versao or 0) + 1
    sala.save(update_fields=[*campos, 'versao'])
    return sala


@transaction.atomic
def iniciar(sala):
    sala = _travar(sala)
    if sala.fase != Sala.Fase.AGENDADA:
        raise ErroJogo('Esta sala já começou ou foi encerrada.')
    if not perguntas_da_sala(sala):
        raise ErroJogo('O quiz desta sala não tem perguntas.')
    momento = agora()
    return _mudar(sala, fase=Sala.Fase.PERGUNTA, pergunta_atual=0, pergunta_inicio=momento,
                  iniciada_em=momento)


@transaction.atomic
def revelar(sala):
    """Encerra a pergunta no ar e mostra o resultado dela (sem erro se já estava assim)."""
    sala = _travar(sala)
    if sala.fase != Sala.Fase.PERGUNTA:
        return sala
    return _mudar(sala, fase=Sala.Fase.RESULTADO)


@transaction.atomic
def proxima(sala):
    """Do resultado para a próxima pergunta — ou, depois da última, para o pódio."""
    sala = _travar(sala)
    if sala.fase != Sala.Fase.RESULTADO:
        raise ErroJogo('Mostre o resultado desta pergunta antes de passar para a próxima.')
    if sala.pergunta_atual + 1 >= len(perguntas_da_sala(sala)):
        return _mudar(sala, fase=Sala.Fase.ENCERRADA, encerrada_em=agora())
    return _mudar(sala, fase=Sala.Fase.PERGUNTA, pergunta_atual=sala.pergunta_atual + 1,
                  pergunta_inicio=agora())


@transaction.atomic
def encerrar(sala):
    """Termina a partida antes do fim (o que foi respondido até aqui vale)."""
    sala = _travar(sala)
    if sala.fase == Sala.Fase.AGENDADA:
        raise ErroJogo('A partida ainda não começou: cancele a sala em vez de encerrar.')
    if sala.fase in (Sala.Fase.ENCERRADA, Sala.Fase.CANCELADA):
        return sala
    return _mudar(sala, fase=Sala.Fase.ENCERRADA, encerrada_em=agora())


@transaction.atomic
def cancelar(sala):
    sala = _travar(sala)
    if sala.fase != Sala.Fase.AGENDADA:
        raise ErroJogo('Só dá para cancelar antes de começar.')
    return _mudar(sala, fase=Sala.Fase.CANCELADA)


def todos_responderam(sala, pergunta):
    """Todo mundo que entrou na sala já respondeu a pergunta no ar?"""
    presentes = sala.participantes.filter(entrou_em__isnull=False).count()
    if not presentes:
        return False
    respondidas = Resposta.objects.filter(pergunta=pergunta, participante__sala=sala,
                                          participante__entrou_em__isnull=False,
                                          respondida_em__isnull=False).count()
    return respondidas >= presentes


def fechar_se_acabou(sala, momento=None, conferir_respostas=True):
    """Pergunta vencida (ou já respondida por todos) vira resultado. Roda a cada consulta das telas.

    A consulta do participante não confere as respostas (``conferir_respostas=False``):
    quem fecha a pergunta por "todos responderam" é a última resposta — e a tela
    do responsável, que consulta a cada segundo.
    """
    if sala.fase != Sala.Fase.PERGUNTA:
        return sala
    pergunta = pergunta_no_ar(sala)
    if pergunta is None:
        return sala
    momento = momento or agora()
    if momento > fim_da_pergunta(sala, pergunta) + FOLGA_DA_REDE or (
            conferir_respostas and todos_responderam(sala, pergunta)):
        return revelar(sala)
    return sala


# ---------------------------------------------------------------------------
# Participante
# ---------------------------------------------------------------------------
def marcar_presenca(participante, momento=None):
    """Entrou na sala (uma vez) e está com ela aberta (no máximo uma escrita a cada 10 s)."""
    momento = momento or agora()
    campos = {}
    if participante.entrou_em is None:
        campos['entrou_em'] = momento
    if participante.visto_em is None or momento - participante.visto_em > GRAVAR_VISTO_A_CADA:
        campos['visto_em'] = momento
    if campos:
        Participante.objects.filter(pk=participante.pk).update(**campos)
        for campo, valor in campos.items():
            setattr(participante, campo, valor)
    return participante


def ver_pergunta(participante, pergunta, momento=None):
    """Marca quando a pergunta chegou na tela desta pessoa: o relógio dela começa aqui."""
    resposta, _ = Resposta.objects.get_or_create(participante=participante, pergunta=pergunta,
                                                 defaults={'vista_em': momento or agora()})
    return resposta


def responder(sala, participante, indice, alternativa_id, momento=None):
    """Registra a resposta (uma só) e devolve a Resposta. ErroJogo quando não vale."""
    momento = momento or agora()
    with transaction.atomic():
        atual = Sala.objects.filter(pk=sala.pk).values('fase', 'pergunta_atual', 'pergunta_inicio').get()
        if atual['fase'] != Sala.Fase.PERGUNTA or atual['pergunta_atual'] != indice:
            raise ErroJogo('O tempo desta pergunta acabou.')
        pergunta = perguntas_da_sala(sala)[indice]
        alternativa = next((a for a in pergunta.alternativas.all() if a.pk == alternativa_id), None)
        if alternativa is None:
            raise ErroJogo('Escolha uma das alternativas da pergunta.')
        resposta, _ = (Resposta.objects.select_for_update()
                       .get_or_create(participante=participante, pergunta=pergunta,
                                      defaults={'vista_em': atual['pergunta_inicio']}))
        if resposta.respondida_em:
            raise ErroJogo('Você já respondeu esta pergunta.')
        limite = timedelta(seconds=pergunta.tempo_limite)
        vista = max(resposta.vista_em, atual['pergunta_inicio'])
        if momento > vista + limite + timedelta(seconds=1) or momento > atual['pergunta_inicio'] + limite + FOLGA_DA_REDE:
            raise ErroJogo('O tempo desta pergunta acabou.')
        tempo_ms = int(max(0.0, min((momento - vista).total_seconds(), float(pergunta.tempo_limite))) * 1000)
        resposta.alternativa = alternativa
        resposta.respondida_em = momento
        resposta.tempo_ms = tempo_ms
        resposta.correta = alternativa.correta
        resposta.pontos = pontos_por(alternativa.correta, tempo_ms, pergunta.tempo_limite)
        resposta.save(update_fields=['alternativa', 'respondida_em', 'tempo_ms', 'correta', 'pontos'])
        Participante.objects.filter(pk=participante.pk).update(
            pontos=F('pontos') + resposta.pontos,
            acertos=F('acertos') + (1 if resposta.correta else 0),
            erros=F('erros') + (0 if resposta.correta else 1),
            tempo_acertos_ms=F('tempo_acertos_ms') + (tempo_ms if resposta.correta else 0),
            entrou_em=F('entrou_em') if participante.entrou_em else momento)
    # Fora da transação: se era o último a responder, a sala já vai para o resultado.
    atualizada = Sala.objects.get(pk=sala.pk)
    atualizada._perguntas = perguntas_da_sala(sala)
    fechar_se_acabou(atualizada, momento)
    return resposta


# ---------------------------------------------------------------------------
# Placar
# ---------------------------------------------------------------------------
def ranking(sala):
    """Quem entrou na sala, na ordem do placar, com a posição (1, 2, 3...)."""
    lista = list(sala.participantes.filter(entrou_em__isnull=False).select_related('user', 'user__sector')
                 .order_by('-pontos', 'tempo_acertos_ms', 'user__first_name', 'user__last_name', 'id'))
    for posicao, participante in enumerate(lista, start=1):
        participante.posicao = posicao
    return lista


def distribuicao(sala, pergunta):
    """{alternativa_id: quantos marcaram} entre os participantes desta sala."""
    return dict(Resposta.objects.filter(pergunta=pergunta, participante__sala=sala, alternativa__isnull=False)
                .values_list('alternativa_id').annotate(total=Count('id')).values_list('alternativa_id', 'total'))


def placar_do_passo(sala):
    """O ranking (resumido) e, no resultado, quantos marcaram cada alternativa.

    Dentro de um passo da partida (``Sala.versao``) nada disso muda: é montado
    uma vez e servido às telas de todo mundo por alguns segundos.
    """
    chave = f'quiz:placar:{sala.pk}:{sala.versao}'
    cache = caches['local']
    try:
        guardado = cache.get(chave)
    except Exception:                                           # noqa: BLE001 — sem cache, calcula
        guardado = None
    if guardado is not None:
        return guardado
    pergunta = pergunta_no_ar(sala) if sala.fase == Sala.Fase.RESULTADO else None
    guardado = {
        'placar': [{'pk': p.pk, 'nome': p.nome, 'pontos': p.pontos, 'posicao': p.posicao,
                    'acertos': p.acertos, 'erros': p.erros} for p in ranking(sala)],
        'contagem': distribuicao(sala, pergunta) if pergunta is not None else {},
    }
    try:
        cache.set(chave, guardado, PLACAR_NA_MEMORIA_POR)
    except Exception:                                           # noqa: BLE001
        pass
    return guardado


def _pessoa(item):
    return {'nome': item['nome'], 'pontos': item['pontos'], 'posicao': item['posicao']}


def _alternativas(pergunta, com_resultado=False, contagem=None):
    saida = []
    for n, alternativa in enumerate(pergunta.alternativas.all()):
        item = {'id': alternativa.pk, 'texto': alternativa.texto, 'n': n}
        if com_resultado:
            item['correta'] = alternativa.correta
            item['total'] = (contagem or {}).get(alternativa.pk, 0)
        saida.append(item)
    return saida


# ---------------------------------------------------------------------------
# O que cada tela recebe
# ---------------------------------------------------------------------------
def _base(sala, momento):
    return {
        'fase': sala.fase, 'versao': sala.versao, 'titulo': sala.titulo, 'codigo': sala.codigo,
        'total_perguntas': len(perguntas_da_sala(sala)),
        'agendada_para': timezone.localtime(sala.agendada_para).strftime('%d/%m/%Y às %H:%M'),
        'agora_ms': int(momento.timestamp() * 1000),
    }


def estado_participante(sala, participante, momento=None):
    momento = momento or agora()
    sala = fechar_se_acabou(sala, momento, conferir_respostas=False)
    if sala.aberta:
        # Abrir o link de uma sala encerrada (ou cancelada) não é ter jogado nela.
        marcar_presenca(participante, momento)
    dados = _base(sala, momento)
    pergunta = pergunta_no_ar(sala)
    if sala.fase == Sala.Fase.AGENDADA:
        dados['presentes'] = sala.participantes.filter(entrou_em__isnull=False).count()
        return dados
    if sala.fase == Sala.Fase.PERGUNTA and pergunta is not None:
        resposta = ver_pergunta(participante, pergunta, momento)
        vista = max(resposta.vista_em, sala.pergunta_inicio)
        fim = min(vista + timedelta(seconds=pergunta.tempo_limite), fim_da_pergunta(sala, pergunta) + FOLGA_DA_REDE)
        dados.update({
            'indice': sala.pergunta_atual, 'enunciado': pergunta.enunciado,
            'tempo_limite': pergunta.tempo_limite,
            'restante_ms': max(0, int((fim - momento).total_seconds() * 1000)),
            'alternativas': _alternativas(pergunta),
            'respondida': resposta.respondida_em is not None,
            'escolhida': resposta.alternativa_id,
        })
        return dados
    if sala.fase in (Sala.Fase.RESULTADO, Sala.Fase.ENCERRADA):
        passo = placar_do_passo(sala)
        placar = passo['placar']
        eu = next((p for p in placar if p['pk'] == participante.pk), None)
        dados.update({'pontos_total': eu['pontos'] if eu else 0, 'posicao': eu['posicao'] if eu else None,
                      'presentes': len(placar), 'top': [_pessoa(p) for p in placar[:TOP_DO_PLACAR]]})
        if sala.fase == Sala.Fase.RESULTADO and pergunta is not None:
            minha = Resposta.objects.filter(participante=participante, pergunta=pergunta).first()
            dados.update({
                'indice': sala.pergunta_atual, 'enunciado': pergunta.enunciado,
                'alternativas': _alternativas(pergunta, True, passo['contagem']),
                'minha': {'respondida': bool(minha and minha.respondida_em), 'correta': bool(minha and minha.correta),
                          'pontos': minha.pontos if minha else 0,
                          'alternativa': minha.alternativa_id if minha else None},
                'ultima': sala.pergunta_atual + 1 >= dados['total_perguntas'],
            })
        if sala.fase == Sala.Fase.ENCERRADA:
            dados.update({'podio': [_pessoa(p) for p in placar[:3]],
                          'acertos': eu['acertos'] if eu else 0, 'erros': eu['erros'] if eu else 0})
    return dados


def estado_responsavel(sala, momento=None):
    momento = momento or agora()
    sala = fechar_se_acabou(sala, momento)
    dados = _base(sala, momento)
    pergunta = pergunta_no_ar(sala)
    if sala.fase == Sala.Fase.AGENDADA:
        pessoas = list(sala.participantes.select_related('user').order_by('user__first_name', 'user__last_name'))
        dados.update({
            'convidados': len(pessoas),
            'presentes': sum(1 for p in pessoas if p.entrou_em),
            'pessoas': [{'nome': p.nome, 'entrou': bool(p.entrou_em),
                         'online': bool(p.visto_em and momento - p.visto_em < ONLINE_POR)} for p in pessoas],
        })
        return dados
    presentes = sala.participantes.filter(entrou_em__isnull=False).count()
    dados['presentes'] = presentes
    if sala.fase == Sala.Fase.PERGUNTA and pergunta is not None:
        respondidas = Resposta.objects.filter(pergunta=pergunta, participante__sala=sala,
                                              respondida_em__isnull=False).count()
        dados.update({
            'indice': sala.pergunta_atual, 'enunciado': pergunta.enunciado, 'tempo_limite': pergunta.tempo_limite,
            'restante_ms': max(0, int((fim_da_pergunta(sala, pergunta) - momento).total_seconds() * 1000)),
            'alternativas': _alternativas(pergunta), 'respondidas': respondidas,
        })
        return dados
    passo = placar_do_passo(sala)
    placar = passo['placar']
    if sala.fase == Sala.Fase.RESULTADO and pergunta is not None:
        contagem = passo['contagem']
        dados.update({
            'indice': sala.pergunta_atual, 'enunciado': pergunta.enunciado,
            'alternativas': _alternativas(pergunta, True, contagem),
            'respondidas': sum(contagem.values()),
            'top': [_pessoa(p) for p in placar[:TOP_DO_PLACAR]],
            'ultima': sala.pergunta_atual + 1 >= dados['total_perguntas'],
        })
    if sala.fase == Sala.Fase.ENCERRADA:
        dados.update({'podio': [_pessoa(p) for p in placar[:3]], 'ranking': [_pessoa(p) for p in placar]})
    return dados


def acertos_por_pergunta(sala):
    """Por pergunta: acertos, erros e sem resposta entre quem entrou — para o resultado do gestor."""
    presentes = sala.participantes.filter(entrou_em__isnull=False).count()
    linhas = []
    contagem = {r['pergunta_id']: r for r in (
        Resposta.objects.filter(participante__sala=sala, participante__entrou_em__isnull=False)
        .values('pergunta_id')
        .annotate(acertos=Count('id', filter=Q(correta=True)),
                  erros=Count('id', filter=Q(respondida_em__isnull=False, correta=False))))}
    for n, pergunta in enumerate(perguntas_da_sala(sala), start=1):
        c = contagem.get(pergunta.pk, {})
        acertos, erros = c.get('acertos', 0), c.get('erros', 0)
        sem_resposta = max(presentes - acertos - erros, 0)
        linhas.append({'n': n, 'pergunta': pergunta, 'acertos': acertos, 'erros': erros,
                       'sem_resposta': sem_resposta,
                       'taxa_erro': ((erros + sem_resposta) / presentes) if presentes else 0.0})
    return linhas
