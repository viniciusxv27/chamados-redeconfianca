"""Progresso (plano de carreira): a jornada do consultor e o painel do SUPERADMIN."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import Sector

from . import regras
from .models import Advertencia, ConfigCarreira, LojaApta, PedidoPromocao, PerfilCarreira

User = get_user_model()


def _so_superadmin(request):
    if regras.e_superadmin(request.user):
        return None
    messages.error(request, 'Só o SUPERADMIN mexe no Plano de Carreira.')
    return redirect('carreira:jornada')


def _lojas():
    return Sector.objects.filter(name__icontains='loja').order_by('name')


def _comercial():
    """Os consultores do painel: só quem o /quadro-hc/ mostra como "Consultor" (24/11/2026).

    Antes era todo PADRAO com setor de loja — entravam gerente, ADM, recepção e sniper.
    Consultor pleno fica de fora: o Progresso é a jornada até Pleno.
    """
    from quadro_hc.servicos import ids_por_funcao
    return (User.objects.filter(is_active=True, pk__in=ids_por_funcao({'Consultor'}))
            .select_related('sector', 'perfil_carreira').order_by('first_name', 'last_name'))


def _decimal(texto, minimo=None, maximo=None):
    texto = (texto or '').strip().replace('%', '').replace(',', '.')
    if not texto:
        return None
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        raise ValueError(f'"{texto}" não é um número.')
    if (minimo is not None and valor < minimo) or (maximo is not None and valor > maximo):
        raise ValueError(f'{texto} está fora do intervalo ({minimo} a {maximo}).')
    return valor


# ---------------------------------------------------------------------------
# O consultor
# ---------------------------------------------------------------------------
@login_required
def jornada(request):
    user = request.user
    if not regras.pode_ver_modulo(user):
        messages.error(request, 'O Plano de Carreira ainda não está liberado para você.')
        return redirect('home')
    if regras.e_superadmin(user) and not regras.e_do_comercial(user):
        return redirect('carreira:painel')
    dados = regras.avaliar(user)
    regras.guardar_retrato(dados)
    novo = regras.pedir_promocao_se_concluiu(user, dados)
    return render(request, 'carreira/jornada.html', {
        **dados, 'pessoa': user, 'novo_pedido': bool(novo), 'eu_mesmo': True,
    })


@login_required
def certificado(request, user_id=None):
    alvo = request.user if user_id is None else get_object_or_404(User, pk=user_id)
    if alvo != request.user and not regras.e_superadmin(request.user):
        raise Http404
    if alvo == request.user and not regras.pode_ver_modulo(request.user):
        raise Http404
    dados = regras.avaliar(alvo)
    if not (dados['concluiu'] or dados['pleno']):
        messages.info(request, 'O certificado sai quando todos os requisitos estiverem cumpridos.')
        return redirect('carreira:jornada' if alvo == request.user else 'carreira:pessoa', *([] if alvo == request.user else [alvo.pk]))
    pedido = dados['pedido']
    return render(request, 'carreira/certificado.html', {
        **dados, 'pessoa': alvo,
        'tipo': 'promocao' if dados['pleno'] else 'jornada',
        'data': (dados['perfil'].promovido_em or (pedido.criado_em if pedido else timezone.now())),
        'codigo': f'RC-{alvo.pk:05d}-{(pedido.pk if pedido else 0):04d}',
    })


# ---------------------------------------------------------------------------
# O SUPERADMIN
# ---------------------------------------------------------------------------
@login_required
def painel(request):
    if (barra := _so_superadmin(request)) is not None:
        return barra
    from knowledge_trails.models import KnowledgeTrail
    config = ConfigCarreira.atual()
    pessoas = list(_comercial())
    for p in pessoas:
        perfil = getattr(p, 'perfil_carreira', None)
        p.retrato = regras.retrato_atual(perfil.retrato if perfil else {})
        p.nivel = perfil.get_nivel_display() if perfil else 'Consultor Jr'
    pessoas.sort(key=lambda p: (-(p.retrato.get('cumpridos') or 0), (p.first_name or '').upper()))
    return render(request, 'carreira/painel.html', {
        'config': config,
        'trilhas': KnowledgeTrail.objects.order_by('title'),
        'trilhas_escolhidas': set(config.trilhas.values_list('pk', flat=True)),
        'pedidos': PedidoPromocao.objects.filter(situacao=PedidoPromocao.ANALISE).select_related('user', 'user__sector'),
        'decididos': PedidoPromocao.objects.exclude(situacao=PedidoPromocao.ANALISE)
                                           .select_related('user', 'decidido_por')[:15],
        'pessoas': pessoas,
        'resultado_em': PerfilCarreira.objects.aggregate(m=Max('resultado_auto_em'))['m'],
    })


@login_required
@require_POST
def liberar(request):
    if (barra := _so_superadmin(request)) is not None:
        return barra
    config = ConfigCarreira.atual()
    config.liberado = request.POST.get('liberado') == '1'
    config.atualizado_por = request.user
    config.save()
    from django.core.cache import cache
    cache.delete(regras.CHAVE_LIBERADO)
    messages.success(request, 'Plano de Carreira liberado para o comercial: os consultores já veem a jornada no menu.'
                     if config.liberado else 'Plano de Carreira escondido: só o SUPERADMIN vê.')
    return redirect('carreira:painel')


@login_required
@require_POST
def atualizar_resultado(request):
    """Relê o comissionamento pós-contestação dos últimos 6 meses para todo o comercial (numa thread)."""
    if (barra := _so_superadmin(request)) is not None:
        return barra
    from .resultado import atualizar_em_segundo_plano
    if atualizar_em_segundo_plano():
        messages.success(request, 'Lendo o comissionamento pós-contestação dos últimos 6 meses (cerca de um minuto). '
                                  'Recarregue a página daqui a pouco.')
    else:
        messages.info(request, 'A atualização já está rodando.')
    return redirect('carreira:painel')


@login_required
@require_POST
def trilhas(request):
    if (barra := _so_superadmin(request)) is not None:
        return barra
    from knowledge_trails.models import KnowledgeTrail
    config = ConfigCarreira.atual()
    config.trilhas.set(KnowledgeTrail.objects.filter(pk__in=request.POST.getlist('trilhas')))
    config.atualizado_por = request.user
    config.save()
    messages.success(request, f'Formação: {config.trilhas.count()} trilha(s) contam para o Pleno.')
    return redirect('carreira:painel')


@login_required
@require_POST
def lojas_aptas(request):
    if (barra := _so_superadmin(request)) is not None:
        return barra
    try:
        ano, tri = int(request.POST['ano']), int(request.POST['tri'])
    except (KeyError, ValueError):
        messages.error(request, 'Trimestre inválido.')
        return redirect('carreira:painel')
    from django.urls import reverse
    voltar = f"{reverse('carreira:painel')}?ano={ano}&tri={tri}#lojas"
    escolhas = []
    for setor in _lojas():
        escolha = request.POST.get(f'apta_{setor.pk}', '')
        try:
            rent = _decimal(request.POST.get(f'rent_{setor.pk}'), Decimal('-999'), Decimal('999'))
        except ValueError as erro:
            messages.error(request, f'Nada salvo — {setor.name}: {erro}')
            return redirect(voltar)
        escolhas.append((setor, escolha, rent))
    salvos = 0
    with transaction.atomic():
        for setor, escolha, rent in escolhas:
            if escolha not in ('1', '0'):
                LojaApta.objects.filter(setor=setor, ano=ano, trimestre=tri).delete()
                continue
            LojaApta.objects.update_or_create(setor=setor, ano=ano, trimestre=tri, defaults={
                'apta': escolha == '1', 'rentabilidade': rent, 'atualizado_por': request.user})
            salvos += 1
    messages.success(request, f'{tri}º trimestre de {ano}: {salvos} loja(s) avaliada(s).')
    return redirect(voltar)


@login_required
def pessoa(request, user_id):
    if (barra := _so_superadmin(request)) is not None:
        return barra
    alvo = get_object_or_404(User, pk=user_id)
    dados = regras.avaliar(alvo, para_superadmin=True)
    regras.guardar_retrato(dados)
    regras.pedir_promocao_se_concluiu(alvo, dados)
    return render(request, 'carreira/jornada.html', {
        **dados, 'pessoa': alvo, 'eu_mesmo': False, 'modo_superadmin': True,
        'advertencias': Advertencia.objects.filter(user=alvo).select_related('registrado_por'),
        'pedidos': PedidoPromocao.objects.filter(user=alvo).select_related('decidido_por'),
    })


@login_required
@require_POST
def pessoa_salvar(request, user_id):
    if (barra := _so_superadmin(request)) is not None:
        return barra
    alvo = get_object_or_404(User, pk=user_id)
    perfil = regras.perfil_de(alvo)
    try:
        promocao = (request.POST.get('ultima_promocao') or '').strip()
        perfil.ultima_promocao = (timezone.datetime.strptime(promocao, '%Y-%m-%d').date() if promocao else None)
        perfil.resultado_media = _decimal(request.POST.get('resultado_media'), Decimal('0'), Decimal('999'))
        perfil.resultado_referencia = (request.POST.get('resultado_referencia') or '').strip()[:60]
        perfil.satisfacao_nota = _decimal(request.POST.get('satisfacao_nota'), Decimal('0'), Decimal('5'))
        perfil.qualidade_manual = _decimal(request.POST.get('qualidade_manual'), Decimal('0'), Decimal('100'))
        perfil.assiduidade_manual = _decimal(request.POST.get('assiduidade_manual'), Decimal('0'), Decimal('100'))
    except ValueError as erro:
        messages.error(request, f'Não salvei: {erro}')
        return redirect('carreira:pessoa', alvo.pk)
    perfil.atualizado_por = request.user
    perfil.save()
    messages.success(request, 'Dados da jornada salvos.')
    return redirect('carreira:pessoa', alvo.pk)


@login_required
@require_POST
def advertencia(request, user_id):
    if (barra := _so_superadmin(request)) is not None:
        return barra
    alvo = get_object_or_404(User, pk=user_id)
    if request.POST.get('apagar'):
        Advertencia.objects.filter(pk=request.POST['apagar'], user=alvo).delete()
        messages.success(request, 'Advertência apagada.')
        return redirect('carreira:pessoa', alvo.pk)
    try:
        dia = timezone.datetime.strptime(request.POST.get('data') or '', '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, 'Informe a data da advertência.')
        return redirect('carreira:pessoa', alvo.pk)
    Advertencia.objects.create(user=alvo, data=dia, motivo=(request.POST.get('motivo') or '').strip()[:2000],
                               registrado_por=request.user)
    messages.success(request, 'Advertência registrada.')
    return redirect('carreira:pessoa', alvo.pk)


@login_required
@require_POST
def decidir(request, pedido_id):
    if (barra := _so_superadmin(request)) is not None:
        return barra
    pedido = get_object_or_404(PedidoPromocao, pk=pedido_id, situacao=PedidoPromocao.ANALISE)
    decisao = request.POST.get('decisao')
    parecer = (request.POST.get('parecer') or '').strip()[:2000]
    if decisao not in (PedidoPromocao.PROMOVIDO, PedidoPromocao.NAO_AGORA):
        messages.error(request, 'Escolha promover ou ainda não.')
        return redirect('carreira:painel')
    if decisao == PedidoPromocao.NAO_AGORA and len(parecer) < 5:
        messages.error(request, 'Para "ainda não", escreva o parecer: é ele que o consultor lê.')
        return redirect('carreira:pessoa', pedido.user_id)
    agora = timezone.now()
    with transaction.atomic():
        pedido.situacao, pedido.parecer = decisao, parecer
        pedido.decidido_por, pedido.decidido_em = request.user, agora
        pedido.save()
        nome = pedido.user.first_name or pedido.user.full_name
        if decisao == PedidoPromocao.PROMOVIDO:
            perfil = regras.perfil_de(pedido.user)
            perfil.nivel, perfil.promovido_em = PerfilCarreira.PLENO, agora
            perfil.ultima_promocao = agora.date()
            perfil.atualizado_por = request.user
            perfil.save()
            regras.avisar_pessoa(pedido.user, 'Parabéns, você é Consultor Pleno! 🎉',
                                 f'{nome}, sua promoção a Pleno foi aprovada. Seu certificado já está no Plano de Carreira.')
            messages.success(request, f'{pedido.user.full_name} promovido a Pleno. Lembre de atualizar o cargo no cadastro.')
        else:
            regras.avisar_pessoa(pedido.user, 'Sua jornada até Pleno',
                                 f'{nome}, o pedido foi analisado: ainda não. {parecer}')
            messages.success(request, 'Decisão registrada: ainda não. O consultor recebeu o parecer.')
    return redirect('carreira:painel')
