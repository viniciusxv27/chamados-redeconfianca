"""Telas das solicitações ao DP: as do colaborador, a caixa do DP e os responsáveis."""
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import servicos
from .models import Anexo, ResponsavelDP, Solicitacao
from .permissoes import e_analista, e_superadmin, pode_ver

S = Solicitacao
User = get_user_model()


def _base(request, aba, **extra):
    return {'aba': aba, 'esconder_abas_ponto': False, 'dp_analista_tela': e_analista(request.user), **extra}


# ── Colaborador ─────────────────────────────────────────────────────────────
@login_required
def minhas(request):
    lista = S.objects.filter(colaborador=request.user).prefetch_related('anexos')
    status = request.GET.get('status', '')
    contagem = dict(lista.values_list('status').annotate(n=Count('id')).values_list('status', 'n'))
    if status == 'abertas':
        lista = lista.filter(status__in=S.ABERTOS)
    elif status in dict(S.STATUS):
        lista = lista.filter(status=status)
    return render(request, 'solicitacoes_dp/minhas.html', _base(
        request, 'solicitacoes', pagina=Paginator(lista, 20).get_page(request.GET.get('pagina')), status=status,
        contagem=contagem, abertas=sum(contagem.get(s, 0) for s in S.ABERTOS),
        devolvidas=contagem.get(S.DEVOLVIDA, 0)))


def _contexto_form(request, tipo, sol=None, dados=None, erros=None):
    tipo_rotulo = dict(S.TIPOS)[tipo]
    especies = S.ESPECIES_ATESTADO if tipo == S.ATESTADO else S.ESPECIES_DECLARACAO
    horas = {}
    if dados is not None:
        for m in dados.get('marcacoes') or []:
            if m['rotulo'] in servicos.ROTULOS_MARCACAO:
                horas[servicos.ROTULOS_MARCACAO.index(m['rotulo'])] = m['hora']
    elif sol is not None:
        for m in sol.marcacoes or []:
            if m.get('rotulo') in servicos.ROTULOS_MARCACAO:
                horas[servicos.ROTULOS_MARCACAO.index(m['rotulo'])] = m.get('hora', '')
    valores = dados if dados is not None else ({
        'data': sol.data, 'data_fim': sol.data_fim, 'hora_inicio': sol.hora_inicio, 'hora_fim': sol.hora_fim,
        'especie': sol.especie, 'motivo_ajuste': sol.motivo_ajuste, 'justificativa': sol.justificativa} if sol else {})
    return _base(request, 'solicitacoes', tipo=tipo, tipo_rotulo=tipo_rotulo, sol=sol, valores=valores, erros=erros or [],
                 especies=especies, motivos=S.MOTIVOS_AJUSTE,
                 linhas_horario=[(i, r, horas.get(i, '')) for i, r in enumerate(servicos.ROTULOS_MARCACAO)],
                 anexos=sol.anexos.filter(removido=False) if sol else [], hoje=timezone.localdate(),
                 tamanho_max=servicos.TAMANHO_MAX_MB, anexos_max=servicos.ANEXOS_MAX)


@login_required
def nova(request, tipo):
    tipo = tipo.upper()
    if tipo not in dict(S.TIPOS):
        raise Http404
    if request.method == 'POST':
        dados = servicos.ler(tipo, request.POST)
        arquivos = request.FILES.getlist('anexos')
        try:
            sol = servicos.criar(request.user, dados, arquivos)
        except servicos.Regra as erro:
            return render(request, 'solicitacoes_dp/form.html', _contexto_form(request, tipo, dados=dados, erros=erro.args[0]))
        messages.success(request, f'Solicitação #{sol.pk} enviada ao DP. Você é avisado aqui no portal a cada passo.')
        return redirect('solicitacoes_dp:detalhe', pk=sol.pk)
    dados = None
    if request.GET.get('data'):
        dados = {'data': servicos._data(request.GET['data']), 'marcacoes': []}
    return render(request, 'solicitacoes_dp/form.html', _contexto_form(request, tipo, dados=dados))


@login_required
def corrigir(request, pk):
    sol = get_object_or_404(S, pk=pk, colaborador=request.user)
    if sol.status not in (S.DEVOLVIDA, S.ENVIADA):
        messages.error(request, 'Esta solicitação não pode mais ser corrigida.')
        return redirect('solicitacoes_dp:detalhe', pk=sol.pk)
    if request.method == 'POST':
        dados = servicos.ler(sol.tipo, request.POST)
        dados['comentario'] = request.POST.get('comentario', '')
        try:
            servicos.corrigir(sol, request.user, dados, request.FILES.getlist('anexos'), request.POST.getlist('remover'))
        except servicos.Regra as erro:
            return render(request, 'solicitacoes_dp/form.html', _contexto_form(request, sol.tipo, sol=sol, dados=dados,
                                                                                erros=erro.args[0]))
        messages.success(request, 'Correção enviada ao DP.')
        return redirect('solicitacoes_dp:detalhe', pk=sol.pk)
    return render(request, 'solicitacoes_dp/form.html', _contexto_form(request, sol.tipo, sol=sol))


@login_required
def marcacoes_do_dia(request):
    """O que o relógio registrou no dia, para o colaborador conferir antes de pedir o ajuste (só o próprio ponto)."""
    try:
        dia = date.fromisoformat(request.GET.get('data', ''))
    except ValueError:
        return JsonResponse({'horas': None})
    return JsonResponse({'horas': servicos.ponto_registrado(request.user, dia)})


# ── Detalhe e ações ─────────────────────────────────────────────────────────
def _solicitacao_visivel(request, pk):
    sol = get_object_or_404(S.objects.select_related('colaborador', 'colaborador__sector', 'responsavel'), pk=pk)
    if not pode_ver(request.user, sol):
        raise Http404
    return sol


@login_required
def detalhe(request, pk):
    sol = _solicitacao_visivel(request, pk)
    dp = e_analista(request.user)
    dono = sol.colaborador_id == request.user.pk
    registrado = servicos.ponto_registrado(sol.colaborador, sol.data) if sol.tipo == S.AJUSTE else None
    outras = (S.objects.filter(colaborador=sol.colaborador).exclude(pk=sol.pk).order_by('-enviada_em')[:5] if dp else [])
    return render(request, 'solicitacoes_dp/detalhe.html', _base(
        request, 'caixa_dp' if dp and not dono else 'solicitacoes', sol=sol, dp=dp, dono=dono, registrado=registrado,
        anexos=sol.anexos.filter(removido=False), anexos_removidos=sol.anexos.filter(removido=True),
        eventos=sol.eventos.select_related('autor'), outras=outras,
        pode_corrigir=dono and sol.status in (S.DEVOLVIDA, S.ENVIADA),
        pode_cancelar=dono and sol.status in (S.ENVIADA, S.DEVOLVIDA),
        pode_analisar=dp and sol.status in (S.ENVIADA, S.EM_ANALISE),
        pode_lancar=dp and sol.tipo == S.AJUSTE and sol.status == S.APROVADA and not sol.ajustado_no_ponto))


@login_required
@require_POST
def acao(request, pk):
    sol = _solicitacao_visivel(request, pk)
    acao = request.POST.get('acao')
    texto = request.POST.get('texto', '')
    try:
        if acao == 'cancelar':
            servicos.cancelar(sol, request.user, texto)
            messages.success(request, 'Solicitação cancelada.')
        elif acao == 'assumir':
            servicos.assumir(sol, request.user)
            messages.success(request, 'Análise iniciada: o colaborador foi avisado.')
        elif acao == 'aprovar':
            servicos.aprovar(sol, request.user, texto, arquivar=request.POST.get('arquivar') == '1',
                             ajustado=request.POST.get('ajustado') == '1')
            messages.success(request, 'Solicitação aprovada: o colaborador foi avisado.')
        elif acao == 'devolver':
            servicos.devolver(sol, request.user, texto)
            messages.success(request, 'Devolvida para correção: o colaborador foi avisado.')
        elif acao == 'lancar':
            servicos.lancar_ajuste(sol, request.user)
            messages.success(request, 'Ajuste marcado como lançado no ponto.')
        elif acao == 'comentar':
            servicos.comentar(sol, request.user, texto)
            messages.success(request, 'Comentário registrado.')
        else:
            messages.error(request, 'Ação desconhecida.')
    except servicos.Regra as erro:
        for e in erro.args[0]:
            messages.error(request, e)
    voltar = request.POST.get('voltar', '')
    if acao in ('aprovar', 'devolver') and voltar.startswith('/ponto/dp/'):
        return redirect(voltar)
    return redirect('solicitacoes_dp:detalhe', pk=sol.pk)


@login_required
def anexo(request, pk, anexo_pk):
    sol = _solicitacao_visivel(request, pk)
    arquivo = get_object_or_404(Anexo, pk=anexo_pk, solicitacao=sol)
    try:
        conteudo = arquivo.arquivo.open('rb')
    except (FileNotFoundError, OSError):
        raise Http404('Arquivo não encontrado.')
    resposta = FileResponse(conteudo, as_attachment=request.GET.get('baixar') == '1',
                            filename=arquivo.nome_original or 'anexo', content_type=arquivo.content_type or None)
    resposta['Cache-Control'] = 'private, max-age=300'
    resposta['X-Content-Type-Options'] = 'nosniff'
    return resposta


# ── Caixa do DP ─────────────────────────────────────────────────────────────
FILTROS = [('pendentes', 'Pendentes'), ('devolvidas', 'Devolvidas'), ('lancar', 'Ajustes a lançar'),
           ('aprovadas', 'Aprovadas'), ('todas', 'Todas')]


@login_required
def caixa(request):
    if not e_analista(request.user):
        messages.error(request, 'A caixa do DP é para quem analisa as solicitações.')
        return redirect('solicitacoes_dp:minhas')
    base = S.objects.select_related('colaborador', 'colaborador__sector', 'responsavel')
    hoje = timezone.localdate()
    inicio_mes = hoje.replace(day=1)
    kpis = {
        'aguardando': base.filter(status=S.ENVIADA).count(),
        'em_analise': base.filter(status=S.EM_ANALISE).count(),
        'devolvidas': base.filter(status=S.DEVOLVIDA).count(),
        'lancar': base.filter(tipo=S.AJUSTE, status=S.APROVADA, ajustado_no_ponto=False).count(),
        'aprovadas_mes': base.filter(status=S.APROVADA, aprovada_em__date__gte=inicio_mes).count(),
        'atrasadas': base.filter(status__in=(S.ENVIADA, S.EM_ANALISE), enviada_em__lt=timezone.now() - timedelta(days=2)).count(),
    }
    filtro = request.GET.get('filtro', 'pendentes')
    lista = base
    if filtro == 'pendentes':
        lista = lista.filter(status__in=(S.ENVIADA, S.EM_ANALISE)).order_by('enviada_em')
    elif filtro == 'devolvidas':
        lista = lista.filter(status=S.DEVOLVIDA)
    elif filtro == 'lancar':
        lista = lista.filter(tipo=S.AJUSTE, status=S.APROVADA, ajustado_no_ponto=False).order_by('aprovada_em')
    elif filtro == 'aprovadas':
        lista = lista.filter(status=S.APROVADA).order_by('-aprovada_em')
    tipo = request.GET.get('tipo', '')
    if tipo in dict(S.TIPOS):
        lista = lista.filter(tipo=tipo)
    q = (request.GET.get('q') or '').strip()
    if q:
        filtro_q = Q(colaborador__first_name__icontains=q) | Q(colaborador__last_name__icontains=q) | Q(colaborador__email__icontains=q)
        if q.lstrip('#').isdigit():
            filtro_q |= Q(pk=int(q.lstrip('#')))
        lista = lista.filter(filtro_q)
    de, ate = servicos._data(request.GET.get('de')), servicos._data(request.GET.get('ate'))
    if de:
        lista = lista.filter(data__gte=de)
    if ate:
        lista = lista.filter(data__lte=ate)
    return render(request, 'solicitacoes_dp/caixa.html', _base(
        request, 'caixa_dp', kpis=kpis, filtro=filtro, filtros=FILTROS, tipo=tipo, tipos=S.TIPOS, q=q, de=de, ate=ate,
        pagina=Paginator(lista, 30).get_page(request.GET.get('pagina')), voltar=request.get_full_path(),
        e_superadmin=e_superadmin(request.user)))


@login_required
def responsaveis(request):
    if not e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN escolhe os responsáveis do DP.')
        return redirect('solicitacoes_dp:caixa')
    if request.method == 'POST':
        acao = request.POST.get('acao')
        if acao == 'adicionar':
            alvo = User.objects.filter(pk=request.POST.get('user') or 0, is_active=True).first()
            if alvo:
                ResponsavelDP.objects.get_or_create(user=alvo, defaults={'criado_por': request.user})
                messages.success(request, f'{alvo.get_full_name()} agora analisa e recebe os avisos.')
            else:
                messages.error(request, 'Escolha a pessoa.')
        elif acao == 'avisos':
            r = ResponsavelDP.objects.filter(pk=request.POST.get('id')).first()
            if r:
                r.recebe_avisos = not r.recebe_avisos
                r.save(update_fields=['recebe_avisos'])
        elif acao == 'remover':
            ResponsavelDP.objects.filter(pk=request.POST.get('id')).delete()
            messages.success(request, 'Responsável removido.')
        return redirect('solicitacoes_dp:responsaveis')
    from .permissoes import destinatarios
    return render(request, 'solicitacoes_dp/responsaveis.html', _base(
        request, 'caixa_dp', lista=ResponsavelDP.objects.select_related('user', 'user__sector'),
        destinatarios=destinatarios(),
        pessoas=User.objects.filter(is_active=True).order_by('first_name', 'last_name').only('id', 'first_name', 'last_name', 'username')))

