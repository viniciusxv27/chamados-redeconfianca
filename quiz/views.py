"""Telas do Quiz.

Participante: início (salas dele e histórico), entrar com código, a tela de jogo
(que consulta ``estado`` a cada segundo e manda ``responder``) e o resultado.

Gestão (SUPERADMIN e quem tem ``quiz.gestao``): quizzes, perguntas, banco de
perguntas, salas (com a seleção de participantes), o painel ao vivo do
responsável, os resultados da sala e os relatórios — com exportação para Excel.
"""
import json
import logging
from datetime import datetime
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from core.models import NotificationMixin

from . import jogo, publico, relatorios
from .models import (ALTERNATIVAS_MAX, ALTERNATIVAS_MIN, TEMPO_MAXIMO, TEMPO_MINIMO, Alternativa,
                     Participante, Pergunta, Quiz, Sala)
from .permissoes import e_superadmin, participacao, pode_conduzir, pode_gerenciar

logger = logging.getLogger(__name__)
User = get_user_model()

CORES = ['vermelho', 'azul', 'amarelo', 'verde']         # as alternativas, como no Kahoot
FORMAS = ['▲', '◆', '●', '■']


def _notificar(usuarios, titulo, mensagem, url):
    """Só dentro do portal (sino). Falhar aqui nunca derruba a tela."""
    try:
        NotificationMixin.create_notifications_for_users(
            users=list(usuarios), title=titulo, message=mensagem, notification_type='SYSTEM', related_url=url)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Aviso do quiz não foi criado: %s', exc)


def _inteiro(valor, minimo=None, maximo=None, padrao=None):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    if minimo is not None and numero < minimo:
        return minimo
    if maximo is not None and numero > maximo:
        return maximo
    return numero


def so_gestao(view):
    @wraps(view)
    @login_required
    def envoltorio(request, *args, **kwargs):
        if not pode_gerenciar(request.user):
            messages.error(request, 'Criar e administrar quizzes é liberado pelo SUPERADMIN.')
            return redirect('quiz:inicio')
        return view(request, *args, **kwargs)
    return envoltorio


def _sala(codigo):
    return get_object_or_404(Sala.objects.select_related('quiz', 'responsavel'), codigo=(codigo or '').upper())


def _url_da_sala(sala):
    return reverse('quiz:jogar', args=[sala.codigo])


def _quando(sala):
    return timezone.localtime(sala.agendada_para).strftime('%d/%m às %H:%M')


def _contexto(request, aba, **extra):
    return {'aba': aba, 'pode_gerenciar': pode_gerenciar(request.user), **extra}


# ===========================================================================
# Participante
# ===========================================================================
@login_required
def inicio(request):
    minhas = list(Participante.objects.filter(user=request.user)
                  .select_related('sala', 'sala__quiz', 'sala__responsavel')
                  .exclude(sala__fase=Sala.Fase.CANCELADA))
    abertas = sorted((p for p in minhas if p.sala.aberta), key=lambda p: p.sala.agendada_para)
    historico = sorted((p for p in minhas if p.sala.encerrada),
                       key=lambda p: p.sala.agendada_para, reverse=True)[:30]
    ctx = _contexto(request, 'inicio', abertas=abertas, historico=historico)
    if ctx['pode_gerenciar']:
        # O gestor vê, logo na entrada, as salas que ele conduz (o SUPERADMIN, todas as abertas).
        conduzo = Sala.objects.filter(fase__in=Sala.ABERTAS)
        if not e_superadmin(request.user):
            conduzo = conduzo.filter(responsavel=request.user)
        ctx['minhas_salas'] = (conduzo.select_related('quiz').annotate(convidados=Count('participantes'))
                               .order_by('agendada_para')[:10])
    return render(request, 'quiz/inicio.html', ctx)


@login_required
@require_POST
def entrar(request):
    codigo = ''.join((request.POST.get('codigo') or '').split()).upper()
    sala = Sala.objects.filter(codigo=codigo).first() if codigo else None
    if sala is None:
        messages.error(request, f'Não existe sala com o código "{codigo}". Confira as letras e os números.')
        return redirect('quiz:inicio')
    if participacao(request.user, sala):
        return redirect('quiz:jogar', codigo=sala.codigo)
    if pode_conduzir(request.user, sala):
        return redirect('quiz:painel', codigo=sala.codigo)
    messages.error(request, 'Você não foi chamado para esta sala.')
    return redirect('quiz:inicio')


@login_required
def jogar(request, codigo):
    sala = _sala(codigo)
    participante = participacao(request.user, sala)
    if participante is None:
        if pode_conduzir(request.user, sala):
            return redirect('quiz:painel', codigo=sala.codigo)
        messages.error(request, 'Você não foi chamado para esta sala.')
        return redirect('quiz:inicio')
    if sala.fase == Sala.Fase.CANCELADA:
        messages.info(request, f'A sala "{sala.titulo}" foi cancelada.')
        return redirect('quiz:inicio')
    if sala.encerrada:
        if participante.entrou_em:
            return redirect('quiz:meu_resultado', codigo=sala.codigo)
        messages.info(request, f'A partida "{sala.titulo}" já terminou — você não chegou a entrar nela.')
        return redirect('quiz:inicio')
    return render(request, 'quiz/jogar.html', _contexto(
        request, 'inicio', sala=sala, participante=participante, cores=CORES, formas=FORMAS))


@login_required
def estado(request, codigo):
    sala = _sala(codigo)
    participante = participacao(request.user, sala)
    if participante is None:
        return JsonResponse({'ok': False, 'erro': 'Você não foi chamado para esta sala.'}, status=403)
    dados = jogo.estado_participante(sala, participante)
    if dados['fase'] == Sala.Fase.ENCERRADA and participante.entrou_em:
        dados['url_resultado'] = reverse('quiz:meu_resultado', args=[sala.codigo])
    return JsonResponse({'ok': True, **dados})


@login_required
@require_POST
def responder(request, codigo):
    sala = _sala(codigo)
    participante = participacao(request.user, sala)
    if participante is None:
        return JsonResponse({'ok': False, 'erro': 'Você não foi chamado para esta sala.'}, status=403)
    try:
        corpo = json.loads(request.body or b'{}')
    except ValueError:
        corpo = {}
    indice = _inteiro(corpo.get('indice'))
    alternativa = _inteiro(corpo.get('alternativa'))
    if indice is None or alternativa is None:
        return JsonResponse({'ok': False, 'erro': 'Escolha uma alternativa.'}, status=400)
    try:
        jogo.responder(sala, participante, indice, alternativa)
    except jogo.ErroJogo as exc:
        return JsonResponse({'ok': False, 'erro': str(exc)}, status=409)
    return JsonResponse({'ok': True})


@login_required
def meu_resultado(request, codigo):
    sala = _sala(codigo)
    participante = participacao(request.user, sala)
    if participante is None:
        return redirect('quiz:inicio')
    if not sala.encerrada:
        return redirect('quiz:jogar', codigo=sala.codigo)
    if participante.entrou_em is None:
        messages.info(request, f'Você não participou da partida "{sala.titulo}".')
        return redirect('quiz:inicio')
    return render(request, 'quiz/resultado_participante.html', _contexto(
        request, 'inicio', sala=sala, participante=participante,
        r=relatorios.resultado_do_participante(sala, participante)))


# ===========================================================================
# Gestão: quizzes e perguntas
# ===========================================================================
@so_gestao
def quizzes(request):
    q = (request.GET.get('q') or '').strip()
    situacao = request.GET.get('situacao') or ''
    lista = (Quiz.objects.select_related('criado_por')
             .annotate(total_perguntas=Count('perguntas', distinct=True), total_salas=Count('salas', distinct=True)))
    for termo in q.split():
        lista = lista.filter(Q(titulo__icontains=termo) | Q(categoria__icontains=termo)
                             | Q(descricao__icontains=termo))
    if situacao in Quiz.Status.values:
        lista = lista.filter(status=situacao)
    return render(request, 'quiz/quizzes.html', _contexto(
        request, 'quizzes', quizzes=lista.order_by('-atualizado_em'), q=q, situacao=situacao))


def _dados_do_quiz(request):
    return {
        'titulo': (request.POST.get('titulo') or '').strip()[:150],
        'descricao': (request.POST.get('descricao') or '').strip(),
        'categoria': (request.POST.get('categoria') or '').strip()[:80],
        'tempo_padrao': _inteiro(request.POST.get('tempo_padrao'), TEMPO_MINIMO, TEMPO_MAXIMO, 20),
    }


@so_gestao
def quiz_novo(request):
    if request.method == 'POST':
        dados = _dados_do_quiz(request)
        if not dados['titulo']:
            messages.error(request, 'Dê um nome ao quiz.')
        else:
            quiz = Quiz.objects.create(criado_por=request.user, **dados)
            messages.success(request, 'Quiz criado. Agora cadastre as perguntas.')
            return redirect('quiz:quiz', pk=quiz.pk)
    return render(request, 'quiz/quiz_form.html', _contexto(
        request, 'quizzes', quiz=None, categorias=_categorias(), tempo_min=TEMPO_MINIMO, tempo_max=TEMPO_MAXIMO))


def _categorias():
    return list(Quiz.objects.exclude(categoria='').values_list('categoria', flat=True).distinct().order_by('categoria'))


@so_gestao
def quiz_detalhe(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk)
    perguntas = list(quiz.perguntas.prefetch_related('alternativas'))
    return render(request, 'quiz/quiz_detalhe.html', _contexto(
        request, 'quizzes', quiz=quiz, perguntas=perguntas, cores=CORES, formas=FORMAS,
        problemas=quiz.problemas_para_publicar() if quiz.editavel else [],
        salas=quiz.salas.select_related('responsavel').annotate(convidados=Count('participantes'))[:20],
        ja_jogado=quiz.ja_jogado()))


@so_gestao
def quiz_editar(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk)
    if request.method == 'POST':
        dados = _dados_do_quiz(request)
        if not dados['titulo']:
            messages.error(request, 'Dê um nome ao quiz.')
        else:
            for campo, valor in dados.items():
                setattr(quiz, campo, valor)
            quiz.save()
            messages.success(request, 'Quiz atualizado.')
            return redirect('quiz:quiz', pk=quiz.pk)
    return render(request, 'quiz/quiz_form.html', _contexto(
        request, 'quizzes', quiz=quiz, categorias=_categorias(), tempo_min=TEMPO_MINIMO, tempo_max=TEMPO_MAXIMO))


@so_gestao
@require_POST
def quiz_publicar(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk)
    problemas = quiz.problemas_para_publicar()
    if problemas:
        messages.error(request, 'Ainda não dá para publicar: ' + ' '.join(problemas))
    elif quiz.editavel:
        quiz.status = Quiz.Status.PUBLICADO
        quiz.publicado_em = timezone.now()
        quiz.save(update_fields=['status', 'publicado_em', 'atualizado_em'])
        messages.success(request, 'Quiz publicado — já dá para criar salas com ele. As perguntas ficam travadas.')
    return redirect('quiz:quiz', pk=quiz.pk)


@so_gestao
@require_POST
def quiz_rascunho(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk)
    if quiz.ja_jogado():
        messages.error(request, 'Este quiz já foi jogado: para mudar as perguntas, duplique-o. '
                                'Assim o resultado das salas antigas continua valendo.')
    elif quiz.salas.filter(fase=Sala.Fase.AGENDADA).exists():
        messages.error(request, 'Há sala marcada com este quiz: cancele a sala antes de voltar ao rascunho.')
    else:
        quiz.status = Quiz.Status.RASCUNHO
        quiz.save(update_fields=['status', 'atualizado_em'])
        messages.success(request, 'O quiz voltou a ser rascunho: as perguntas podem mudar de novo.')
    return redirect('quiz:quiz', pk=quiz.pk)


def _copiar_pergunta(pergunta, quiz, ordem, origem=None):
    nova = Pergunta.objects.create(quiz=quiz, ordem=ordem, enunciado=pergunta.enunciado,
                                   tempo_limite=pergunta.tempo_limite, explicacao=pergunta.explicacao,
                                   origem=origem)
    Alternativa.objects.bulk_create([Alternativa(pergunta=nova, ordem=a.ordem, texto=a.texto, correta=a.correta)
                                     for a in pergunta.alternativas.all()])
    return nova


@so_gestao
@require_POST
def quiz_duplicar(request, pk):
    original = get_object_or_404(Quiz, pk=pk)
    with transaction.atomic():
        copia = Quiz.objects.create(titulo=f'{original.titulo} (cópia)'[:150], descricao=original.descricao,
                                    categoria=original.categoria, tempo_padrao=original.tempo_padrao,
                                    duplicado_de=original, criado_por=request.user)
        for n, pergunta in enumerate(original.perguntas.prefetch_related('alternativas')):
            _copiar_pergunta(pergunta, copia, n)
    messages.success(request, 'Cópia criada como rascunho — mude o que precisar e publique.')
    return redirect('quiz:quiz', pk=copia.pk)


@so_gestao
@require_POST
def quiz_excluir(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk)
    if quiz.salas.exists():
        messages.error(request, 'Este quiz tem salas (e resultados) e não pode ser excluído.')
        return redirect('quiz:quiz', pk=quiz.pk)
    titulo = quiz.titulo
    quiz.delete()
    messages.success(request, f'Quiz "{titulo}" excluído.')
    return redirect('quiz:quizzes')


def _quiz_editavel_ou_volta(request, quiz):
    if quiz.editavel:
        return None
    messages.error(request, 'Quiz publicado não muda as perguntas. Volte-o para rascunho (se nunca foi jogado) '
                            'ou duplique-o.')
    return redirect('quiz:quiz', pk=quiz.pk)


def _salvar_pergunta(request, quiz, pergunta=None):
    """Grava a pergunta do formulário. Devolve (pergunta, erro)."""
    enunciado = (request.POST.get('enunciado') or '').strip()[:500]
    textos = [(t or '').strip()[:200] for t in request.POST.getlist('alternativas')][:ALTERNATIVAS_MAX]
    correta = _inteiro(request.POST.get('correta'))
    preenchidas = [(n, texto) for n, texto in enumerate(textos) if texto]
    if not enunciado:
        return None, 'Escreva a pergunta.'
    if len(preenchidas) < ALTERNATIVAS_MIN:
        return None, f'Preencha pelo menos {ALTERNATIVAS_MIN} alternativas.'
    if correta is None or correta >= len(textos) or not textos[correta]:
        return None, 'Marque qual alternativa é a correta.'
    tempo = _inteiro(request.POST.get('tempo_limite'), TEMPO_MINIMO, TEMPO_MAXIMO, quiz.tempo_padrao)
    with transaction.atomic():
        if pergunta is None:
            ultima = quiz.perguntas.aggregate(m=Max('ordem'))['m']
            pergunta = Pergunta(quiz=quiz, ordem=(ultima + 1) if ultima is not None else 0)
        pergunta.enunciado = enunciado
        pergunta.tempo_limite = tempo
        pergunta.explicacao = (request.POST.get('explicacao') or '').strip()[:500]
        pergunta.save()
        pergunta.alternativas.all().delete()
        Alternativa.objects.bulk_create([
            Alternativa(pergunta=pergunta, ordem=ordem, texto=texto, correta=(n == correta))
            for ordem, (n, texto) in enumerate(preenchidas)])
    return pergunta, None


def _form_pergunta(request, quiz, pergunta=None, erro=''):
    if pergunta is not None and request.method != 'POST':
        alternativas = [(a.texto, a.correta) for a in pergunta.alternativas.all()]
    else:
        textos = request.POST.getlist('alternativas') if request.method == 'POST' else []
        marcada = _inteiro(request.POST.get('correta')) if request.method == 'POST' else 0
        alternativas = [(texto, n == marcada) for n, texto in enumerate(textos)]
    while len(alternativas) < ALTERNATIVAS_MAX:
        alternativas.append(('', False))
    return render(request, 'quiz/pergunta_form.html', _contexto(
        request, 'quizzes', quiz=quiz, pergunta=pergunta, erro=erro, alternativas=alternativas,
        cores=CORES, formas=FORMAS, tempo_min=TEMPO_MINIMO, tempo_max=TEMPO_MAXIMO,
        valores={'enunciado': request.POST.get('enunciado', pergunta.enunciado if pergunta else ''),
                 'tempo_limite': request.POST.get('tempo_limite',
                                                  pergunta.tempo_limite if pergunta else quiz.tempo_padrao),
                 'explicacao': request.POST.get('explicacao', pergunta.explicacao if pergunta else '')}))


@so_gestao
def pergunta_nova(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk)
    volta = _quiz_editavel_ou_volta(request, quiz)
    if volta:
        return volta
    if request.method == 'POST':
        pergunta, erro = _salvar_pergunta(request, quiz)
        if not erro:
            messages.success(request, 'Pergunta adicionada.')
            if request.POST.get('mais'):
                return redirect('quiz:pergunta_nova', pk=quiz.pk)
            return redirect('quiz:quiz', pk=quiz.pk)
        return _form_pergunta(request, quiz, erro=erro)
    return _form_pergunta(request, quiz)


@so_gestao
def pergunta_editar(request, pk):
    pergunta = get_object_or_404(Pergunta.objects.select_related('quiz'), pk=pk)
    volta = _quiz_editavel_ou_volta(request, pergunta.quiz)
    if volta:
        return volta
    if request.method == 'POST':
        _, erro = _salvar_pergunta(request, pergunta.quiz, pergunta)
        if not erro:
            messages.success(request, 'Pergunta atualizada.')
            return redirect('quiz:quiz', pk=pergunta.quiz_id)
        return _form_pergunta(request, pergunta.quiz, pergunta, erro)
    return _form_pergunta(request, pergunta.quiz, pergunta)


@so_gestao
@require_POST
def pergunta_duplicar(request, pk):
    pergunta = get_object_or_404(Pergunta.objects.select_related('quiz').prefetch_related('alternativas'), pk=pk)
    volta = _quiz_editavel_ou_volta(request, pergunta.quiz)
    if volta:
        return volta
    with transaction.atomic():
        pergunta.quiz.perguntas.filter(ordem__gt=pergunta.ordem).update(ordem=F('ordem') + 1)
        _copiar_pergunta(pergunta, pergunta.quiz, pergunta.ordem + 1)
    messages.success(request, 'Pergunta duplicada logo abaixo da original.')
    return redirect('quiz:quiz', pk=pergunta.quiz_id)


@so_gestao
@require_POST
def pergunta_excluir(request, pk):
    pergunta = get_object_or_404(Pergunta.objects.select_related('quiz'), pk=pk)
    volta = _quiz_editavel_ou_volta(request, pergunta.quiz)
    if volta:
        return volta
    quiz_id = pergunta.quiz_id
    pergunta.delete()
    messages.success(request, 'Pergunta excluída.')
    return redirect('quiz:quiz', pk=quiz_id)


@so_gestao
@require_POST
def pergunta_mover(request, pk):
    pergunta = get_object_or_404(Pergunta.objects.select_related('quiz'), pk=pk)
    volta = _quiz_editavel_ou_volta(request, pergunta.quiz)
    if volta:
        return volta
    perguntas = list(pergunta.quiz.perguntas.all())
    i = next(n for n, p in enumerate(perguntas) if p.pk == pergunta.pk)
    j = i - 1 if request.POST.get('direcao') == 'cima' else i + 1
    if 0 <= j < len(perguntas):
        perguntas[i], perguntas[j] = perguntas[j], perguntas[i]
        with transaction.atomic():
            for ordem, p in enumerate(perguntas):
                if p.ordem != ordem:
                    Pergunta.objects.filter(pk=p.pk).update(ordem=ordem)
    return redirect(f"{reverse('quiz:quiz', args=[pergunta.quiz_id])}#pergunta-{pergunta.pk}")


@so_gestao
def banco(request, pk):
    """Banco de perguntas: as dos outros quizzes, para reaproveitar neste (vão como cópia)."""
    quiz = get_object_or_404(Quiz, pk=pk)
    volta = _quiz_editavel_ou_volta(request, quiz)
    if volta:
        return volta
    if request.method == 'POST':
        ids = [i for i in (_inteiro(x) for x in request.POST.getlist('perguntas')) if i]
        escolhidas = (Pergunta.objects.filter(pk__in=ids).exclude(quiz=quiz)
                      .prefetch_related('alternativas').order_by('quiz_id', 'ordem'))
        ultima = quiz.perguntas.aggregate(m=Max('ordem'))['m']
        proxima = (ultima + 1) if ultima is not None else 0
        with transaction.atomic():
            copiadas = 0
            for pergunta in escolhidas:
                _copiar_pergunta(pergunta, quiz, proxima + copiadas, origem=pergunta.origem or pergunta)
                copiadas += 1
        messages.success(request, f'{copiadas} pergunta(s) reaproveitada(s) do banco.' if copiadas
                         else 'Nenhuma pergunta escolhida.')
        return redirect('quiz:quiz', pk=quiz.pk)
    q = (request.GET.get('q') or '').strip()
    categoria = (request.GET.get('categoria') or '').strip()
    perguntas = (Pergunta.objects.exclude(quiz=quiz).select_related('quiz').prefetch_related('alternativas')
                 .order_by('-quiz__atualizado_em', 'quiz_id', 'ordem'))
    for termo in q.split():
        perguntas = perguntas.filter(Q(enunciado__icontains=termo) | Q(quiz__titulo__icontains=termo))
    if categoria:
        perguntas = perguntas.filter(quiz__categoria=categoria)
    # A mesma pergunta reaproveitada em vários quizzes aparece uma vez só (a primeira de cada enunciado).
    vistas, unicas = set(), []
    for pergunta in perguntas[:400]:
        chave = pergunta.enunciado.strip().lower()
        if chave in vistas:
            continue
        vistas.add(chave)
        unicas.append(pergunta)
    ja_no_quiz = {p.enunciado.strip().lower() for p in quiz.perguntas.all()}
    return render(request, 'quiz/banco.html', _contexto(
        request, 'quizzes', quiz=quiz, perguntas=unicas[:150], q=q, categoria=categoria,
        categorias=_categorias(), ja_no_quiz=ja_no_quiz))


# ===========================================================================
# Gestão: salas
# ===========================================================================
@so_gestao
def salas(request):
    situacao = request.GET.get('situacao') or 'abertas'
    lista = (Sala.objects.select_related('quiz', 'responsavel')
             .annotate(convidados=Count('participantes', distinct=True),
                       presentes=Count('participantes', filter=Q(participantes__entrou_em__isnull=False),
                                       distinct=True)))
    if situacao == 'encerradas':
        lista = lista.filter(fase=Sala.Fase.ENCERRADA).order_by('-agendada_para')
    elif situacao == 'canceladas':
        lista = lista.filter(fase=Sala.Fase.CANCELADA).order_by('-agendada_para')
    else:
        situacao = 'abertas'
        lista = lista.filter(fase__in=Sala.ABERTAS).order_by('agendada_para')
    return render(request, 'quiz/salas.html', _contexto(request, 'salas', salas=lista[:200], situacao=situacao))


def _ler_data_hora(texto):
    try:
        momento = datetime.strptime((texto or '').strip(), '%Y-%m-%dT%H:%M')
    except ValueError:
        return None
    return timezone.make_aware(momento, timezone.get_current_timezone())


def _form_sala(request, sala=None, erro=''):
    catalogo = publico.catalogo(request.user)
    marcados_pessoas = set()
    if sala is not None:
        marcados_pessoas = set(sala.participantes.values_list('user_id', flat=True))
    if request.method == 'POST':
        marcados_pessoas = {i for i in (_inteiro(x) for x in request.POST.getlist('usuarios')) if i}
        # Volta com erro: o que estava marcado continua marcado.
        escolhidos = publico.escolhidos_do_post(request.POST)
        for caminho, itens in catalogo.items():
            marcados = {str(x) for x in escolhidos.get(caminho, [])}
            for item in itens:
                item['marcado'] = str(item['id']) in marcados
    pessoas = (User.objects.filter(is_active=True).exclude(pk=request.user.pk).select_related('sector')
               .order_by('first_name', 'last_name'))
    agendada = request.POST.get('agendada_para') if request.method == 'POST' else (
        timezone.localtime(sala.agendada_para).strftime('%Y-%m-%dT%H:%M') if sala else '')
    return render(request, 'quiz/sala_form.html', _contexto(
        request, 'salas', sala=sala, erro=erro, catalogo=catalogo, pessoas=pessoas,
        marcados_pessoas=marcados_pessoas,
        abas=[(caminho, rotulo, catalogo[caminho]) for caminho, rotulo in (
            ('lojas', 'Lojas'), ('setores', 'Setores'), ('cargos', 'Cargos'), ('grupos', 'Grupos'),
            ('coordenacoes', 'Coordenação'))],
        quizzes=Quiz.objects.filter(status=Quiz.Status.PUBLICADO).annotate(total=Count('perguntas'))
        .order_by('titulo'),
        valores={'quiz': request.POST.get('quiz', sala.quiz_id if sala else request.GET.get('quiz', '')),
                 'nome': request.POST.get('nome', sala.nome if sala else ''), 'agendada_para': agendada}))


@so_gestao
def sala_nova(request):
    if request.method != 'POST':
        return _form_sala(request)
    return _salvar_sala(request)


@so_gestao
def sala_editar(request, codigo):
    sala = _sala(codigo)
    if not pode_conduzir(request.user, sala):
        messages.error(request, 'Só o responsável pela sala (ou o SUPERADMIN) muda a sala.')
        return redirect('quiz:salas')
    if sala.fase != Sala.Fase.AGENDADA:
        messages.error(request, 'A sala já começou: não dá mais para mudar data nem participantes.')
        return redirect('quiz:painel', codigo=sala.codigo)
    if request.method != 'POST':
        return _form_sala(request, sala)
    return _salvar_sala(request, sala)


def _salvar_sala(request, sala=None):
    quiz = Quiz.objects.filter(pk=_inteiro(request.POST.get('quiz')), status=Quiz.Status.PUBLICADO).first()
    agendada = _ler_data_hora(request.POST.get('agendada_para'))
    catalogo = publico.catalogo(request.user)
    escolhidos = publico.escolhidos_do_post(request.POST)
    ids = publico.expandir(catalogo, escolhidos)
    ids |= {i for i in (_inteiro(x) for x in request.POST.getlist('usuarios')) if i}
    ids.discard(request.user.pk)
    convidados = list(User.objects.filter(pk__in=ids, is_active=True))
    if quiz is None:
        return _form_sala(request, sala, 'Escolha um quiz publicado.')
    if agendada is None:
        return _form_sala(request, sala, 'Informe a data e o horário.')
    if not convidados:
        return _form_sala(request, sala, 'Selecione quem vai participar (por pessoa, loja, cargo, setor ou grupo).')
    origem = publico.origens(catalogo, escolhidos)
    with transaction.atomic():
        if sala is None:
            sala = Sala.objects.create(quiz=quiz, nome=(request.POST.get('nome') or '').strip()[:150],
                                       agendada_para=agendada, responsavel=request.user)
            novo = True
        else:
            sala.quiz = quiz
            sala.nome = (request.POST.get('nome') or '').strip()[:150]
            sala.agendada_para = agendada
            sala.versao += 1
            sala.save()
            novo = False
        antes = set(sala.participantes.values_list('user_id', flat=True))
        agora_ids = {u.pk for u in convidados}
        # Quem saiu da seleção sai da sala — menos quem já entrou nela.
        sala.participantes.filter(user_id__in=antes - agora_ids, entrou_em__isnull=True).delete()
        chegando = [u for u in convidados if u.pk not in antes]
        Participante.objects.bulk_create([
            Participante(sala=sala, user=u, origem=origem.get(u.pk, (Participante.Origem.MANUAL, ''))[0],
                         rotulo_origem=origem.get(u.pk, ('', ''))[1][:120]) for u in chegando])
    _notificar(chegando, 'Você foi chamado para um quiz',
               f'"{sala.titulo}" — {_quando(sala)}. Código da sala: {sala.codigo}. Entre pelo menu Quiz.',
               _url_da_sala(sala))
    if novo:
        messages.success(request, f'Sala criada com o código {sala.codigo}. {len(convidados)} participante(s) '
                                  f'avisado(s) no portal.')
    else:
        messages.success(request, 'Sala atualizada.' + (f' {len(chegando)} novo(s) participante(s) avisado(s).'
                                                        if chegando else ''))
    return redirect('quiz:painel', codigo=sala.codigo)


# ===========================================================================
# Painel ao vivo do responsável
# ===========================================================================
@so_gestao
def painel(request, codigo):
    sala = _sala(codigo)
    return render(request, 'quiz/painel.html', _contexto(
        request, 'salas', sala=sala, pode_conduzir=pode_conduzir(request.user, sala), cores=CORES, formas=FORMAS,
        url_jogar=request.build_absolute_uri(reverse('quiz:inicio'))))


@so_gestao
def painel_estado(request, codigo):
    sala = _sala(codigo)
    return JsonResponse({'ok': True, **jogo.estado_responsavel(sala)})


@so_gestao
@require_POST
def painel_acao(request, codigo):
    sala = _sala(codigo)
    if not pode_conduzir(request.user, sala):
        return JsonResponse({'ok': False, 'erro': 'Só o responsável pela sala (ou o SUPERADMIN) conduz a partida.'},
                            status=403)
    acao = request.POST.get('acao') or ''
    passos = {'iniciar': jogo.iniciar, 'revelar': jogo.revelar, 'proxima': jogo.proxima,
              'encerrar': jogo.encerrar, 'cancelar': jogo.cancelar}
    if acao not in passos:
        return JsonResponse({'ok': False, 'erro': 'Ação desconhecida.'}, status=400)
    try:
        sala = passos[acao](sala)
    except jogo.ErroJogo as exc:
        return JsonResponse({'ok': False, 'erro': str(exc)}, status=409)
    if acao == 'iniciar':
        fora = User.objects.filter(quiz_participacoes__sala=sala, quiz_participacoes__entrou_em__isnull=True)
        _notificar(fora, 'O quiz começou!', f'"{sala.titulo}" já está rolando — entre agora na sala {sala.codigo}.',
                   _url_da_sala(sala))
    return JsonResponse({'ok': True, **jogo.estado_responsavel(sala)})


# ===========================================================================
# Resultados e relatórios
# ===========================================================================
@so_gestao
def resultados(request, codigo):
    sala = _sala(codigo)
    return render(request, 'quiz/resultados.html', _contexto(
        request, 'salas', sala=sala, r=relatorios.resultado_da_sala(sala)))


def _xlsx(conteudo, nome):
    resposta = HttpResponse(conteudo, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resposta['Content-Disposition'] = f'attachment; filename="{nome}"'
    return resposta


@so_gestao
def resultados_excel(request, codigo):
    sala = _sala(codigo)
    return _xlsx(relatorios.planilha_da_sala(sala), f'quiz-{sala.codigo}-resultado.xlsx')


def _filtros(request):
    return (parse_date(request.GET.get('de') or ''), parse_date(request.GET.get('ate') or ''),
            _inteiro(request.GET.get('quiz')))


@so_gestao
def relatorios_view(request):
    inicio, fim, quiz_id = _filtros(request)
    return render(request, 'quiz/relatorios.html', _contexto(
        request, 'relatorios', r=relatorios.relatorio_geral(inicio, fim, quiz_id),
        quizzes=Quiz.objects.filter(salas__fase=Sala.Fase.ENCERRADA).distinct().order_by('titulo'),
        filtros={'de': request.GET.get('de', ''), 'ate': request.GET.get('ate', ''), 'quiz': quiz_id}))


@so_gestao
def relatorios_excel(request):
    inicio, fim, quiz_id = _filtros(request)
    return _xlsx(relatorios.planilha_geral(relatorios.relatorio_geral(inicio, fim, quiz_id)),
                 f'quiz-relatorio-{timezone.localdate():%Y-%m-%d}.xlsx')
