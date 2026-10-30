import json
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_time
from django.views.decorators.http import require_POST

from communications.models import CommunicationGroup
from users.models import Sector, User

from . import servicos
from .models import AcessoRotinaAdm, ItemChecklist, MarcacaoChecklist
from .permissoes import e_superadmin, exige_acesso, papel, pode_gerir

MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
         'outubro', 'novembro', 'dezembro']
DIAS = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo']


def _loja_escolhida(request):
    """Visualizador: a loja do setor principal. Gestor/SUPERADMIN: a pedida, ou a dele, ou a primeira."""
    proprio = request.user.sector if servicos.e_loja(request.user.sector) else None
    if not pode_gerir(request.user):
        return proprio
    pedido = request.GET.get('loja') or request.POST.get('loja') or ''
    if pedido.isdigit():
        loja = servicos.lojas().filter(pk=int(pedido)).first()
        if loja:
            return loja
    return proprio or servicos.lojas().first()


def _pode_marcar(user, loja):
    return pode_gerir(user) or (loja is not None and user.sector_id == loja.id)


def _segunda(request):
    try:
        dia = date.fromisoformat(request.GET.get('semana') or '')
    except ValueError:
        dia = timezone.localdate()
    return dia - timedelta(days=dia.weekday())


@login_required
@exige_acesso()
def painel(request):
    loja = _loja_escolhida(request)
    gestor = pode_gerir(request.user)
    hoje = timezone.localdate()
    contexto = {
        'loja': loja, 'gestor': gestor, 'papel': papel(request.user), 'superadmin': e_superadmin(request.user),
        'hoje': hoje, 'hoje_extenso': f'{DIAS[hoje.weekday()]}, {hoje.day} de {MESES[hoje.month - 1]} de {hoje.year}',
        'lojas': servicos.lojas() if gestor else [], 'lvut': servicos.LVUT_EXEMPLO, 'aba': 'painel',
    }
    if loja is not None:
        chamados = servicos.chamados_abertos(loja)
        contexto.update({
            'chamados_total': chamados.count(), 'chamados': list(chamados[:30]),
            'fiscal': servicos.pendencias_fiscais(loja),
            'checklist': servicos.itens_do_dia(loja, hoje),
            'pode_marcar': _pode_marcar(request.user, loja),
        })
        feitos = sum(1 for l in contexto['checklist'] if l['marcacao'])
        contexto['checklist_feitos'] = feitos
        contexto['checklist_pct'] = round(feitos * 100 / len(contexto['checklist'])) if contexto['checklist'] else 0

    # Agenda: a Rotina Gerencial de quem está vendo; Gestor/SUPERADMIN podem olhar a de outra pessoa.
    pessoa = request.user
    if gestor and (request.GET.get('pessoa') or '').isdigit():
        pessoa = User.objects.filter(pk=int(request.GET['pessoa']), is_active=True).first() or request.user
    segunda = _segunda(request)
    contexto.update({
        'pessoa': pessoa, 'agenda': servicos.agenda_da_semana(pessoa, segunda),
        'semana_anterior': (segunda - timedelta(days=7)).isoformat(),
        'semana_seguinte': (segunda + timedelta(days=7)).isoformat(),
        'semana_atual': segunda == hoje - timedelta(days=hoje.weekday()),
        'segunda': segunda,
    })
    if gestor:
        contexto['pessoas_com_rotina'] = (User.objects.filter(rotina_gerencial__ativa=True, is_active=True)
                                          .order_by('first_name', 'last_name'))
    return render(request, 'rotina_adm/painel.html', contexto)


@login_required
@exige_acesso()
@require_POST
def checklist_marcar(request, item_id):
    """Marca ou desmarca um item do check-list de hoje."""
    item = get_object_or_404(ItemChecklist, pk=item_id, ativo=True)
    if not _pode_marcar(request.user, item.loja):
        return JsonResponse({'ok': False, 'erro': 'Você só marca o check-list da sua loja.'}, status=403)
    try:
        feito = bool(json.loads(request.body or b'{}').get('feito'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'erro': 'Envio inválido.'}, status=400)
    hoje = timezone.localdate()
    if feito:
        m, _ = MarcacaoChecklist.objects.get_or_create(item=item, data=hoje, defaults={'feito_por': request.user})
        quem = (m.feito_por.full_name if m.feito_por else '')
        return JsonResponse({'ok': True, 'feito': True, 'hora': timezone.localtime(m.feito_em).strftime('%H:%M'),
                             'quem': quem})
    MarcacaoChecklist.objects.filter(item=item, data=hoje).delete()
    return JsonResponse({'ok': True, 'feito': False})


@login_required
@exige_acesso(gerir=True)
def checklist_editar(request):
    """Gestor/SUPERADMIN montam o check-list de cada loja ou copiam o de outra."""
    loja = _loja_escolhida(request)
    if loja is None:
        messages.error(request, 'Nenhuma loja cadastrada.')
        return redirect('rotina_adm:painel')
    volta = f"{reverse('rotina_adm:checklist_editar')}?loja={loja.id}"
    if request.method == 'POST':
        acao = request.POST.get('acao')
        if acao == 'adicionar':
            titulo = (request.POST.get('titulo') or '').strip()[:200]
            if not titulo:
                messages.error(request, 'Escreva o item.')
            else:
                ordem = (ItemChecklist.objects.filter(loja=loja, ativo=True).aggregate(m=Max('ordem'))['m'] or 0) + 1
                ItemChecklist.objects.create(loja=loja, titulo=titulo, horario=parse_time(request.POST.get('horario') or ''),
                                             ordem=ordem, criado_por=request.user)
                messages.success(request, 'Item adicionado.')
        elif acao in ('salvar', 'remover', 'subir', 'descer'):
            item = get_object_or_404(ItemChecklist, pk=request.POST.get('item'), loja=loja, ativo=True)
            if acao == 'salvar':
                titulo = (request.POST.get('titulo') or '').strip()[:200]
                if titulo:
                    item.titulo = titulo
                item.horario = parse_time(request.POST.get('horario') or '')
                item.save(update_fields=['titulo', 'horario', 'atualizado_em'])
                messages.success(request, 'Item salvo.')
            elif acao == 'remover':
                item.ativo = False
                item.save(update_fields=['ativo', 'atualizado_em'])
                messages.success(request, 'Item removido (o histórico de dias anteriores fica).')
            else:
                itens = list(ItemChecklist.objects.filter(loja=loja, ativo=True))
                i = itens.index(item)
                j = i - 1 if acao == 'subir' else i + 1
                if 0 <= j < len(itens):
                    itens[i], itens[j] = itens[j], itens[i]
                    for n, it in enumerate(itens, start=1):
                        if it.ordem != n:
                            it.ordem = n
                            it.save(update_fields=['ordem'])
        elif acao == 'copiar':
            origem = servicos.lojas().filter(pk=request.POST.get('origem') or 0).first()
            if origem is None or origem.id == loja.id:
                messages.error(request, 'Escolha outra loja para copiar.')
            else:
                n = servicos.copiar_checklist(origem, loja, request.user, substituir=request.POST.get('substituir') == 'on')
                messages.success(request, f'{n} item(ns) copiado(s) de {origem.name}.' if n
                                 else f'Nada novo para copiar de {origem.name}.')
        return redirect(volta)

    contagem = dict(ItemChecklist.objects.filter(ativo=True).values_list('loja').annotate(n=Count('id')))
    return render(request, 'rotina_adm/checklist.html', {
        'loja': loja, 'lojas': [(l, contagem.get(l.id, 0)) for l in servicos.lojas()],
        'itens': ItemChecklist.objects.filter(loja=loja, ativo=True),
        'gestor': True, 'superadmin': e_superadmin(request.user), 'aba': 'checklist',
    })


@login_required
def acessos(request):
    """SUPERADMIN libera grupos ou pessoas como Visualizador ou Gestor."""
    if not e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN libera a Rotina ADM.')
        return redirect('home')
    if request.method == 'POST':
        acao = request.POST.get('acao')
        papel_novo = request.POST.get('papel')
        if papel_novo not in dict(AcessoRotinaAdm.PAPEIS):
            papel_novo = AcessoRotinaAdm.VISUALIZADOR
        if acao == 'adicionar':
            alvo = request.POST.get('alvo') or ''
            tipo, _, pk = alvo.partition(':')
            if tipo == 'u' and pk.isdigit() and User.objects.filter(pk=pk, is_active=True).exists():
                AcessoRotinaAdm.objects.update_or_create(user_id=int(pk), defaults={'papel': papel_novo, 'criado_por': request.user})
                messages.success(request, 'Pessoa liberada.')
            elif tipo == 'g' and pk.isdigit() and CommunicationGroup.objects.filter(pk=pk).exists():
                AcessoRotinaAdm.objects.update_or_create(grupo_id=int(pk), defaults={'papel': papel_novo, 'criado_por': request.user})
                messages.success(request, 'Grupo liberado.')
            else:
                messages.error(request, 'Escolha uma pessoa ou um grupo.')
        elif acao in ('papel', 'remover'):
            acesso = get_object_or_404(AcessoRotinaAdm, pk=request.POST.get('acesso'))
            if acao == 'remover':
                acesso.delete()
                messages.success(request, 'Acesso removido.')
            else:
                acesso.papel = papel_novo
                acesso.save(update_fields=['papel', 'atualizado_em'])
                messages.success(request, 'Papel atualizado.')
        return redirect('rotina_adm:acessos')

    lista = list(AcessoRotinaAdm.objects.select_related('user', 'user__sector', 'grupo').order_by('-papel', 'id'))
    for a in lista:
        if a.grupo_id:
            a.membros = a.grupo.members.filter(is_active=True).count()
    usados_u = {a.user_id for a in lista if a.user_id}
    usados_g = {a.grupo_id for a in lista if a.grupo_id}
    return render(request, 'rotina_adm/acessos.html', {
        'acessos': lista, 'papeis': AcessoRotinaAdm.PAPEIS, 'superadmin': True, 'gestor': True, 'aba': 'acessos',
        'usuarios': User.objects.filter(is_active=True).exclude(pk__in=usados_u).select_related('sector')
                                .order_by('first_name', 'last_name'),
        'grupos': CommunicationGroup.objects.exclude(pk__in=usados_g).order_by('name'),
    })
