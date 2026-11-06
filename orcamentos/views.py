"""Telas do módulo de Orçamentos."""
from datetime import date, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import Sector
from vendas import orcamentos as svc
from vendas.models import Orcamento

from . import mailing, painel
from .models import BaseMailing, ContatoMailing, GestorOrcamentos, LiberacaoOrcamentos
from .permissoes import (e_superadmin, lojas_do_vendedor, lojas_geridas, pode_gerir, pode_usar,
                         todas_as_lojas)

User = get_user_model()


def _exige(teste, mensagem):
    def deco(fn):
        @wraps(fn)
        @login_required
        def envolto(request, *a, **kw):
            if not teste(request.user):
                messages.error(request, mensagem)
                return redirect('dashboard')
            return fn(request, *a, **kw)
        return envolto
    return deco


usar = _exige(pode_usar, 'O módulo de Orçamentos é para quem está lotado em loja.')
gerir = _exige(pode_gerir, 'Só o SUPERADMIN e os gestores de orçamentos fazem isso.')
so_superadmin = _exige(e_superadmin, 'Só o SUPERADMIN.')


def _contexto(request, aba, **extra):
    return {'aba': aba, 'pode_gerir': pode_gerir(request.user), 'e_superadmin': e_superadmin(request.user), **extra}


def _contato_do_vendedor(user, contato_id):
    if not contato_id:
        return None
    ids = [l.id for l in lojas_do_vendedor(user)]
    return (ContatoMailing.objects.filter(pk=contato_id, loja_id__in=ids, base__status=BaseMailing.ATIVA)
            .select_related('loja').first())


# ── Orçamento (vendedor) ────────────────────────────────────────────────────
@usar
def inicio(request):
    user = request.user
    preenchido, erros = {}, []
    contato = _contato_do_vendedor(user, request.POST.get('contato') or request.GET.get('contato'))
    if request.method == 'POST':
        por_fetch = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        campos, erros = svc.ler_campos(request.POST)
        if not erros:
            orcamento = svc.ja_enviado(user, campos) or svc.criar(user, campos)
            if contato is not None:
                mailing.marcar_trabalhado(contato, user, orcamento)
            if por_fetch:
                return JsonResponse({'ok': True, 'id': orcamento.pk, 'web': orcamento.link_whatsapp_web,
                                     'app': orcamento.link_whatsapp_app})
            messages.success(request, f'Orçamento de {orcamento.cliente_nome} salvo. Use "Abrir no WhatsApp" na lista para mandar.')
            return redirect('orcamentos:inicio')
        if por_fetch:
            return JsonResponse({'ok': False, 'erros': erros}, status=400)
        preenchido = {c: request.POST.get(c, '') for c in ('cliente_nome', 'pilar', 'numero', 'texto')}
    elif contato is not None:
        preenchido = {'cliente_nome': contato.nome, 'numero': contato.numero_formatado}

    q = (request.GET.get('q') or '').strip()
    lista = Orcamento.objects.select_related('loja').filter(vendedor=user)
    if q:
        digitos = ''.join(c for c in q if c.isdigit())
        filtro = Q(cliente_nome__icontains=q) | Q(texto__icontains=q)
        lista = lista.filter(filtro | Q(numero__contains=digitos) if digitos else filtro)
    lojas = lojas_do_vendedor(user)
    a_trabalhar = ContatoMailing.objects.filter(loja__in=lojas, status=ContatoMailing.NOVO,
                                                base__status=BaseMailing.ATIVA).count()
    return render(request, 'orcamentos/inicio.html', _contexto(
        request, 'inicio', pilares=Orcamento.PILARES, q=q, pagina=Paginator(lista, 25).get_page(request.GET.get('pagina')),
        modelo=svc.modelo_do_vendedor(user), limite=svc.LIMITE_TEXTO, preenchido=preenchido, erros=erros,
        contato=contato, a_trabalhar=a_trabalhar,
        hoje_meus=Orcamento.objects.filter(vendedor=user, criado_em__date=timezone.localdate()).count()))


@login_required
@require_POST
def reabrir(request, pk):
    """Leva de novo à conversa do orçamento (só o próprio vendedor)."""
    orcamento = get_object_or_404(Orcamento, pk=pk, vendedor=request.user)
    return redirect(orcamento.link_whatsapp_web)


# ── Mailing da loja (vendedor) ──────────────────────────────────────────────
@usar
def mailing_loja(request):
    user = request.user
    lojas = lojas_do_vendedor(user)
    loja_id = request.GET.get('loja')
    loja = next((l for l in lojas if str(l.id) == loja_id), None) or (lojas[0] if len(lojas) == 1 else None)
    status = request.GET.get('status') or ContatoMailing.NOVO
    q = (request.GET.get('q') or '').strip()
    qs = ContatoMailing.objects.filter(base__status=BaseMailing.ATIVA).select_related('loja', 'trabalhado_por', 'base')
    qs = qs.filter(loja=loja) if loja else qs.filter(loja__in=lojas)
    contagem = dict(qs.values_list('status').annotate(n=Count('id')).values_list('status', 'n'))
    if status in dict(ContatoMailing.STATUS):
        qs = qs.filter(status=status)
    if q:
        digitos = ''.join(c for c in q if c.isdigit())
        qs = qs.filter(Q(nome__icontains=q) | (Q(numero__contains=digitos) if digitos else Q(pk__in=[])))
    return render(request, 'orcamentos/mailing.html', _contexto(
        request, 'mailing', lojas=lojas, loja=loja, status=status, q=q, contagem=contagem,
        status_opcoes=ContatoMailing.STATUS, pagina=Paginator(qs, 50).get_page(request.GET.get('pagina'))))


@usar
@require_POST
def contato_acao(request, pk):
    contato = _contato_do_vendedor(request.user, pk)
    if contato is None:
        messages.error(request, 'Contato não encontrado no mailing da sua loja.')
        return redirect('orcamentos:mailing')
    acao = request.POST.get('acao')
    if acao == 'descartar':
        contato.status = ContatoMailing.DESCARTADO
        contato.trabalhado_por, contato.trabalhado_em = request.user, timezone.now()
        contato.observacao = (request.POST.get('observacao') or '').strip()[:300]
        contato.save(update_fields=['status', 'trabalhado_por', 'trabalhado_em', 'observacao'])
        messages.success(request, f'{contato.nome or contato.numero_formatado} descartado.')
    elif acao == 'reabrir':
        contato.status = ContatoMailing.NOVO
        contato.trabalhado_por = contato.trabalhado_em = None
        contato.save(update_fields=['status', 'trabalhado_por', 'trabalhado_em'])
        messages.success(request, 'Contato de volta para "a trabalhar".')
    destino = request.POST.get('voltar') or ''
    return redirect(destino if destino.startswith('/orcamentos/') else 'orcamentos:mailing')


# ── Gestão do mailing ───────────────────────────────────────────────────────
@gerir
def gestao(request):
    lojas = lojas_geridas(request.user)
    bases = (BaseMailing.objects.filter(Q(loja__in=lojas) | Q(loja__isnull=True, importado_por=request.user))
             .select_related('loja', 'importado_por')
             .annotate(novos=Count('contatos', filter=Q(contatos__status='NOVO')),
                       trabalhados=Count('contatos', filter=Q(contatos__status='TRABALHADO')),
                       descartados=Count('contatos', filter=Q(contatos__status='DESCARTADO'))))
    return render(request, 'orcamentos/gestao.html', _contexto(request, 'gestao', lojas=lojas, bases=bases,
                                                               limite=mailing.LIMITE_LINHAS))


@gerir
@require_POST
def importar(request):
    try:
        base = mailing.ler_arquivo(request.FILES.get('arquivo'), request.POST.get('nome'), request.user)
    except mailing.ErroMailing as e:
        messages.error(request, str(e))
        return redirect('orcamentos:gestao')
    destino = reverse('orcamentos:conferir', args=[base.pk])
    loja = request.POST.get('loja') or ''
    return redirect(f'{destino}?loja={loja}' if loja.isdigit() else destino)


def _base_gerivel(user, pk):
    lojas = lojas_geridas(user)
    return get_object_or_404(BaseMailing.objects.filter(Q(loja__in=lojas) | Q(loja__isnull=True, importado_por=user)), pk=pk)


@gerir
def conferir(request, pk):
    base = _base_gerivel(request.user, pk)
    if base.status != BaseMailing.RASCUNHO:
        return redirect('orcamentos:gestao')
    lojas = lojas_geridas(request.user)
    if request.method == 'POST':
        def inteiro(campo):
            try:
                return int(request.POST.get(campo))
            except (TypeError, ValueError):
                return None
        loja = next((l for l in lojas if str(l.id) == request.POST.get('loja')), None)
        try:
            mailing.confirmar(base, loja, inteiro('coluna_numero'), inteiro('coluna_nome'), request.user)
        except mailing.ErroMailing as e:
            messages.error(request, str(e))
            return redirect('orcamentos:conferir', pk=base.pk)
        messages.success(request, f'{base.importados} contato(s) enviado(s) para o mailing da {loja.name}'
                                  f' ({base.duplicados} repetido(s) e {base.invalidos} sem número válido ficaram de fora).')
        return redirect('orcamentos:gestao')
    num, nome = mailing.sugerir_colunas(base)
    return render(request, 'orcamentos/conferir.html', _contexto(
        request, 'gestao', base=base, lojas=lojas, previa=mailing.previa(base), sugerida_numero=num,
        sugerida_nome=nome, loja_escolhida=request.GET.get('loja') or ''))


@gerir
@require_POST
def base_acao(request, pk):
    base = _base_gerivel(request.user, pk)
    if request.POST.get('acao') == 'encerrar' and base.status == BaseMailing.ATIVA:
        mailing.encerrar(base)
        messages.success(request, f'Base "{base.nome}" encerrada: os contatos saem da lista da loja.')
    elif request.POST.get('acao') == 'descartar' and base.status == BaseMailing.RASCUNHO:
        base.delete()
        messages.success(request, 'Importação descartada.')
    return redirect('orcamentos:gestao')


# ── Painel analítico ────────────────────────────────────────────────────────
def _periodo(request):
    hoje = timezone.localdate()
    try:
        de = date.fromisoformat(request.GET.get('de') or '')
    except ValueError:
        de = hoje - timedelta(days=29)
    try:
        ate = date.fromisoformat(request.GET.get('ate') or '')
    except ValueError:
        ate = hoje
    if de > ate:
        de, ate = ate, de
    return max(de, ate - timedelta(days=365)), ate


@gerir
def painel_view(request):
    lojas = lojas_geridas(request.user)
    loja_id = request.GET.get('loja')
    recorte = [l for l in lojas if str(l.id) == loja_id] or lojas
    de, ate = _periodo(request)
    dados = painel.montar(recorte, de, ate)
    return render(request, 'orcamentos/painel.html', _contexto(
        request, 'painel', lojas=lojas, loja_id=loja_id or '', de=de, ate=ate, d=dados,
        grafico={'serie': dados['serie'], 'pilares': dados['por_pilar']}))


# ── Acessos (SUPERADMIN) ────────────────────────────────────────────────────
@so_superadmin
def acessos(request):
    if request.method == 'POST':
        acao = request.POST.get('acao')
        alvo = User.objects.filter(pk=request.POST.get('user') or 0, is_active=True).first()
        if acao in ('gestor', 'liberar') and not alvo:
            messages.error(request, 'Escolha a pessoa.')
        elif acao == 'gestor':
            g, _ = GestorOrcamentos.objects.get_or_create(user=alvo, defaults={'criado_por': request.user})
            g.lojas.set(Sector.objects.filter(id__in=request.POST.getlist('lojas')))
            messages.success(request, f'{alvo.get_full_name()} agora é gestor de orçamentos.')
        elif acao == 'tirar_gestor':
            GestorOrcamentos.objects.filter(pk=request.POST.get('id')).delete()
            messages.success(request, 'Gestor removido.')
        elif acao == 'liberar':
            LiberacaoOrcamentos.objects.get_or_create(user=alvo, defaults={'criado_por': request.user})
            messages.success(request, f'{alvo.get_full_name()} pode usar o módulo.')
        elif acao == 'tirar_liberacao':
            LiberacaoOrcamentos.objects.filter(pk=request.POST.get('id')).delete()
            messages.success(request, 'Liberação removida.')
        return redirect('orcamentos:acessos')
    return render(request, 'orcamentos/acessos.html', _contexto(
        request, 'acessos', gestores=GestorOrcamentos.objects.select_related('user').prefetch_related('lojas'),
        liberacoes=LiberacaoOrcamentos.objects.select_related('user'), lojas=todas_as_lojas(),
        pessoas=User.objects.filter(is_active=True).order_by('first_name', 'last_name')
        .only('id', 'first_name', 'last_name', 'username')))
