"""Telas de /bugs/: relatar, acompanhar e — para o SUPERADMIN — aprovar a correção do Claude."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import servicos
from .models import ConfigBugs, RelatoBug

S = RelatoBug.Status
MAX_PRINT = 8 * 1024 * 1024
FILTROS = {
    'abertos': [S.NA_FILA, S.ANALISANDO, S.AGUARDANDO, S.PUBLICANDO, S.FALHA_PUBLICACAO, S.ERRO],
    'aprovar': [S.AGUARDANDO, S.FALHA_PUBLICACAO],
    'publicados': [S.PUBLICADO],
    'encerrados': [S.SEM_CORRECAO, S.REJEITADO, S.PUBLICADO],
}


def _exige_superadmin(user):
    if not servicos.e_superadmin(user):
        raise Http404


def _relato(request, pk):
    return get_object_or_404(servicos.visiveis(request.user), pk=pk)


@login_required
def lista(request):
    servicos.puxar_fila()
    superadmin = servicos.e_superadmin(request.user)
    qs = servicos.visiveis(request.user)
    contagem = {
        'aprovar': qs.filter(status__in=FILTROS['aprovar']).count(),
        'andamento': qs.filter(status__in=[S.NA_FILA, S.ANALISANDO, S.PUBLICANDO]).count(),
        'publicados': qs.filter(status=S.PUBLICADO).count(),
        'total': qs.count(),
    }
    filtro = request.GET.get('f') or ('aprovar' if superadmin and contagem['aprovar'] else 'todos')
    if filtro in FILTROS:
        qs = qs.filter(status__in=FILTROS[filtro])
    if superadmin and request.GET.get('meus') == '1':
        qs = qs.filter(autor=request.user)
    pagina = Paginator(qs, 25).get_page(request.GET.get('p'))
    return render(request, 'bugs/lista.html', {
        'pagina': pagina, 'filtro': filtro, 'contagem': contagem, 'superadmin': superadmin,
        'url_inicial': request.GET.get('url', ''), 'pode_relatar': servicos.pode_relatar_hoje(request.user),
        'config_ok': _config_ok() if superadmin else True,
    })


def _config_ok():
    from . import github
    token, repo, _ = github.configuracao()
    return bool(token and repo)


@login_required
@require_POST
def relatar(request):
    url = (request.POST.get('url') or '').strip()
    descricao = (request.POST.get('descricao') or '').strip()
    arquivo = request.FILES.get('print_tela')
    erro = ''
    if not url:
        erro = 'Informe o endereço (URL) da página onde aconteceu.'
    elif len(descricao) < 10:
        erro = 'Conte com um pouco mais de detalhe o que aconteceu.'
    elif arquivo and (arquivo.size > MAX_PRINT or not (arquivo.content_type or '').startswith('image/')):
        erro = 'O print precisa ser uma imagem de até 8 MB.'
    elif not servicos.pode_relatar_hoje(request.user):
        erro = f'Você já relatou {servicos.LIMITE_DIA} bugs hoje. Amanhã libera de novo.'
    if erro:
        messages.error(request, erro)
        return redirect('bugs:lista')
    relato = servicos.relatar(request.user, url, descricao, arquivo)
    messages.success(request, f'Bug #{relato.pk} enviado. O Claude já está analisando — acompanhe aqui.')
    return redirect('bugs:detalhe', pk=relato.pk)


@login_required
def detalhe(request, pk):
    servicos.conferir_parados()
    relato = _relato(request, pk)
    superadmin = servicos.e_superadmin(request.user)
    return render(request, 'bugs/detalhe.html', {
        'r': relato, 'superadmin': superadmin, 'S': S,
        'config_ok': _config_ok() if superadmin else True,
    })


@login_required
def estado(request, pk):
    """Polling da tela de detalhe enquanto o Claude trabalha."""
    relato = _relato(request, pk)
    return JsonResponse({
        'status': relato.status, 'rotulo': relato.get_status_display(), 'em_andamento': relato.em_andamento,
        'passos': relato.passos[-40:], 'atualizado_em': timezone.localtime(relato.atualizado_em).strftime('%H:%M:%S'),
    })


@login_required
@require_POST
def aprovar(request, pk):
    _exige_superadmin(request.user)
    relato = _relato(request, pk)
    if not _config_ok():
        messages.error(request, 'Configure o GitHub (token e repositório) antes de aprovar.')
        return redirect('bugs:configurar')
    if servicos.aprovar(relato, request.user):
        messages.success(request, 'Aprovado. Publicando no GitHub (commit + push)…')
    else:
        messages.error(request, 'Este bug não está aguardando aprovação.')
    return redirect('bugs:detalhe', pk=pk)


@login_required
@require_POST
def recusar(request, pk):
    _exige_superadmin(request.user)
    relato = _relato(request, pk)
    if servicos.recusar(relato, request.user, request.POST.get('motivo')):
        messages.success(request, 'Correção recusada. Quem relatou foi avisado.')
    else:
        messages.error(request, 'Só dá para recusar uma correção aguardando aprovação.')
    return redirect('bugs:detalhe', pk=pk)


@login_required
@require_POST
def reanalisar(request, pk):
    _exige_superadmin(request.user)
    relato = _relato(request, pk)
    if servicos.pedir_nova_analise(relato, request.user, request.POST.get('orientacao')):
        messages.success(request, 'O Claude vai analisar de novo.')
    else:
        messages.error(request, 'Não dá para pedir nova análise agora (já está em andamento ou publicado).')
    return redirect('bugs:detalhe', pk=pk)


@login_required
def configurar(request):
    _exige_superadmin(request.user)
    from . import github
    cfg = ConfigBugs.get()
    if request.method == 'POST':
        acao = request.POST.get('acao')
        if acao == 'remover_token':
            cfg.set_token('')
        else:
            if request.POST.get('token', '').strip():
                cfg.set_token(request.POST['token'])
            cfg.repositorio = request.POST.get('repositorio', '').strip().strip('/')
            cfg.branch = request.POST.get('branch', '').strip() or 'main'
        cfg.atualizado_por = request.user
        cfg.atualizado_em = timezone.now()
        cfg.save()
        if acao == 'testar' or (acao != 'remover_token' and request.POST.get('token', '').strip()):
            token, repo, branch = github.configuracao()
            try:
                nome = github.Cliente(token, repo).testar(branch)
                messages.success(request, f'Conexão OK: {nome}, branch {branch}, com permissão de push.')
            except github.ErroPublicacao as exc:
                messages.error(request, f'Não conectou: {exc}')
        else:
            messages.success(request, 'Configuração salva.')
        return redirect('bugs:configurar')
    token, repo, branch = github.configuracao()
    return render(request, 'bugs/configurar.html', {
        'cfg': cfg, 'repo_efetivo': repo, 'branch_efetiva': branch, 'tem_token': bool(token),
        'token_do_ambiente': bool(token and not cfg.github_token_cifrado),
    })
