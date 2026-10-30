"""Fechamento de caixa: o dia de cada loja, com prazo até 10h do dia seguinte."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from django.views.decorators.http import require_POST

from users.models import User

from . import fechamento as fch
from .auditoria import diferenca, registrar
from .models import AcessoFechamento, ConfiguracaoFechamento, FechamentoCaixa
from .permissoes import e_superadmin
from .views import Invalido, _contexto_base, dinheiro

DIAS_NO_HISTORICO = 14


def _dia(request):
    """O dia pedido; sem pedido, ontem (a conferência é do dia anterior)."""
    dia = parse_date(request.GET.get('data') or request.POST.get('data') or '')
    hoje = timezone.localdate()
    if dia is None:
        return hoje - timedelta(days=1)
    return min(dia, hoje)


def _url(dia, loja=None):
    return reverse('financeiro:fechamento') + f'?data={dia.isoformat()}' + (f'&loja={loja.id}' if loja else '')


@login_required
def fechamento(request):
    user = request.user
    if not fch.pode_ver(user):
        messages.error(request, 'O Fechamento de caixa não está liberado para o seu usuário.')
        return redirect('home')
    config = ConfiguracaoFechamento.atual()
    dia = _dia(request)
    lojas = list(fch.lojas_de(user))
    existentes = {f.loja_id: f for f in FechamentoCaixa.objects.filter(data=dia, loja__in=lojas)
                  .select_related('atualizado_por', 'bloqueado_por')}
    sugestoes = fch.sugestoes(lojas, dia)
    linhas = []
    for loja in lojas:
        f = existentes.get(loja.id)
        estado, prazo = fch.situacao(dia, f, config)
        linhas.append({'loja': loja, 'f': f, 'estado': estado, 'prazo': prazo, 'sug': sugestoes.get(loja.id, {}),
                       'pode_alterar': fch.pode_alterar(user, dia, f, config)})

    contexto = _contexto_base(request, 'fechamento')
    contexto.update({
        'dia': dia, 'hoje': timezone.localdate(), 'config': config, 'linhas': linhas,
        'dia_anterior': dia - timedelta(days=1), 'dia_seguinte': dia + timedelta(days=1),
        'papel': fch.papel(user), 'pode_editar': fch.pode_editar(user),
        'preenchidos': sum(1 for l in linhas if l['f']), 'bloqueados': sum(1 for l in linhas if l['f'] and l['f'].bloqueado),
        'prazo': fch.prazo_do_dia(dia, config),
        # A faixa de cima fala do dia (não de uma loja): aberto, vencido ou ainda não chegou.
        'estado_dia': fch.situacao(dia, None, config)[0],
        'prazo_passou': timezone.now() > fch.prazo_do_dia(dia, config),
    })
    if len(lojas) == 1:
        # Visualizador (uma loja): os últimos dias da loja dele.
        contexto['historico'] = list(FechamentoCaixa.objects.filter(loja=lojas[0]).order_by('-data')[:DIAS_NO_HISTORICO])
    return render(request, 'financeiro/fechamento.html', contexto)


@login_required
@require_POST
def fechamento_salvar(request):
    user = request.user
    dia = parse_date(request.POST.get('data') or '')
    loja = fch.lojas_de(user).filter(pk=request.POST.get('loja') or 0).first()
    if dia is None or loja is None:
        messages.error(request, 'Loja ou dia inválido.')
        return redirect('financeiro:fechamento')
    config = ConfiguracaoFechamento.atual()
    f = FechamentoCaixa.objects.filter(loja=loja, data=dia).first()
    if not fch.pode_alterar(user, dia, f, config):
        estado, prazo = fch.situacao(dia, f, config)
        motivo = {'bloqueado': 'o dia está bloqueado', 'vencido': f'o prazo acabou em {timezone.localtime(prazo):%d/%m às %H:%M}',
                  'futuro': 'o dia ainda não chegou'}.get(estado, 'seu acesso é só de leitura')
        messages.error(request, f'{loja.name}: não dá para alterar — {motivo}.')
        return redirect(_url(dia))
    try:
        valores = {
            'entradas': dinheiro(request.POST.get('entradas'), campo='entradas'),
            'sangrias': dinheiro(request.POST.get('sangrias'), campo='sangrias'),
            'caixa_fisico': (dinheiro(request.POST.get('caixa_fisico'), campo='caixa físico')
                             if (request.POST.get('caixa_fisico') or '').strip() else None),
            'agoracred': dinheiro(request.POST.get('agoracred'), campo='AgoraCred'),
        }
        bruto = (request.POST.get('renova_allied_qtd') or '0').strip()
        if not bruto.isdigit():
            raise Invalido('A quantidade de Renova Allied tem que ser um número inteiro.')
        valores['renova_allied_qtd'] = int(bruto)
    except Invalido as exc:
        messages.error(request, f'{loja.name}: {exc}')
        return redirect(_url(dia))
    valores['observacao'] = (request.POST.get('observacao') or '').strip()[:2000]
    antes = f.retrato() if f else None
    if f is None:
        f = FechamentoCaixa(loja=loja, data=dia, criado_por=user)
    for campo, valor in valores.items():
        setattr(f, campo, valor)
    f.atualizado_por = user
    f.save()
    a, d = diferenca(antes, f.retrato())
    registrar(request, 'EDITAR' if antes else 'CRIAR', f'Fechamento de caixa {loja.name} {dia:%d/%m/%Y}',
              area='fechamento', obj=f, loja=loja, antes=a, depois=d)
    messages.success(request, f'Fechamento da {loja.name} salvo.')
    return redirect(_url(dia))


@login_required
@require_POST
def fechamento_bloquear(request):
    """Bloqueia (ou desbloqueia) o dia de uma loja. Gestor e SUPERADMIN."""
    user = request.user
    dia = parse_date(request.POST.get('data') or '')
    loja = fch.lojas_de(user).filter(pk=request.POST.get('loja') or 0).first()
    if not fch.pode_editar(user) or dia is None or loja is None:
        messages.error(request, 'Sem permissão para bloquear este fechamento.')
        return redirect('financeiro:fechamento')
    bloquear = request.POST.get('acao') != 'desbloquear'
    f, _ = FechamentoCaixa.objects.get_or_create(loja=loja, data=dia, defaults={'criado_por': user, 'atualizado_por': user})
    f.bloqueado = bloquear
    f.bloqueado_por = user if bloquear else None
    f.bloqueado_em = timezone.now() if bloquear else None
    f.save(update_fields=['bloqueado', 'bloqueado_por', 'bloqueado_em', 'atualizado_em'])
    registrar(request, 'BLOQUEAR' if bloquear else 'DESBLOQUEAR',
              f'{"Bloqueou" if bloquear else "Desbloqueou"} o fechamento {loja.name} {dia:%d/%m/%Y}', area='fechamento', obj=f, loja=loja)
    messages.success(request, f'{loja.name}: dia {"bloqueado" if bloquear else "desbloqueado"}.')
    return redirect(_url(dia))


@login_required
@require_POST
def fechamento_config(request):
    """O botão geral: bloquear (ou não) as alterações depois do prazo, e a hora limite. Só SUPERADMIN."""
    if not e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN muda a regra do prazo.')
        return redirect('financeiro:fechamento')
    config = ConfiguracaoFechamento.atual()
    antes = {'travar_apos_prazo': config.travar_apos_prazo, 'hora_limite': config.hora_limite.strftime('%H:%M')}
    if 'travar_apos_prazo' in request.POST:
        config.travar_apos_prazo = request.POST.get('travar_apos_prazo') == '1'
    hora = parse_time(request.POST.get('hora_limite') or '')
    if hora:
        config.hora_limite = hora
    config.atualizado_por = request.user
    config.save()
    depois = {'travar_apos_prazo': config.travar_apos_prazo, 'hora_limite': config.hora_limite.strftime('%H:%M')}
    registrar(request, 'EDITAR', 'Regra do prazo do fechamento de caixa', area='fechamento', antes=antes, depois=depois)
    messages.success(request, 'Bloqueio depois do prazo ' + ('ligado' if config.travar_apos_prazo else 'desligado')
                     + f' (até {config.hora_limite:%H:%M} do dia seguinte).')
    return redirect(request.POST.get('voltar') if (request.POST.get('voltar') or '').startswith('/financeiro/')
                    else 'financeiro:fechamento')


@login_required
def fechamento_acessos(request):
    """SUPERADMIN libera pessoa a pessoa: Gestor (todas as lojas) ou Visualizador (uma loja)."""
    if not e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN libera o Fechamento de caixa.')
        return redirect('financeiro:fechamento')
    if request.method == 'POST':
        acao = request.POST.get('acao')
        papel = request.POST.get('papel') if request.POST.get('papel') in dict(AcessoFechamento.PAPEIS) else AcessoFechamento.VISUALIZADOR
        loja = fch.lojas_de(request.user).filter(pk=request.POST.get('loja') or 0).first()
        if acao in ('salvar', 'adicionar'):
            alvo = User.objects.filter(pk=request.POST.get('user') or 0, is_active=True).first()
            if alvo is None:
                messages.error(request, 'Escolha a pessoa.')
            elif papel == AcessoFechamento.VISUALIZADOR and loja is None:
                messages.error(request, 'Visualizador precisa de uma loja.')
            else:
                a, criado = AcessoFechamento.objects.update_or_create(
                    user=alvo, defaults={'papel': papel, 'loja': loja if papel == AcessoFechamento.VISUALIZADOR else None,
                                         'liberado_por': request.user})
                registrar(request, 'ACESSO', f'Fechamento de caixa: {alvo.get_full_name()} como {a.get_papel_display()}'
                          + (f' ({loja.name})' if a.loja_id else ''), area='fechamento')
                messages.success(request, f'{alvo.get_full_name()} liberado(a).')
        elif acao == 'remover':
            a = get_object_or_404(AcessoFechamento, pk=request.POST.get('acesso'))
            registrar(request, 'ACESSO', f'Fechamento de caixa: tirou o acesso de {a.user.get_full_name()}', area='fechamento')
            a.delete()
            messages.success(request, 'Acesso removido.')
        return redirect('financeiro:fechamento_acessos')

    acessos = list(AcessoFechamento.objects.select_related('user', 'user__sector', 'loja').order_by('papel', 'user__first_name'))
    usados = {a.user_id for a in acessos}
    contexto = _contexto_base(request, 'fechamento')
    contexto.update({
        'acessos': acessos, 'papeis': AcessoFechamento.PAPEIS, 'lojas': fch.lojas_de(request.user),
        'usuarios': User.objects.filter(is_active=True).exclude(pk__in=usados).select_related('sector').order_by('first_name', 'last_name'),
        'config': ConfiguracaoFechamento.atual(), 'sub': 'acessos',
    })
    return render(request, 'financeiro/fechamento_acessos.html', contexto)
