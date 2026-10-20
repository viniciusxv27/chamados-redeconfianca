"""Busca global do menu superior: os dados do portal que a pessoa pode ver.

Os módulos, funções e configurações vêm do próprio menu, no navegador (o menu
já sai filtrado pelas permissões). Aqui ficam só os registros: chamados,
comunicados, itens do Facilitador e — para quem gerencia usuários — pessoas.
Cada grupo respeita a mesma regra de visibilidade da tela de origem.
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone

LIMITE = 6


def _chamados(user, termo):
    from tickets.models import Ticket
    from tickets.permissions import setores_pelo_setor

    qs = Ticket.objects.all()
    if not user.can_view_all_tickets():
        regra = (Q(created_by=user) | Q(assigned_to=user)
                 | Q(additional_assignments__user=user, additional_assignments__is_active=True))
        if user.can_view_sector_tickets():
            regra |= Q(sector_id__in=list(setores_pelo_setor(user)))
        qs = qs.filter(regra)
    filtro = Q(title__icontains=termo)
    numero = termo.lstrip('#')
    if numero.isdigit():
        filtro |= Q(id=int(numero))
    itens = []
    for t in qs.filter(filtro).select_related('sector').distinct().order_by('-id')[:LIMITE]:
        itens.append({'titulo': f'#{t.id} · {t.title}', 'sub': f'{t.get_status_display()} · {t.sector.name if t.sector_id else ""}',
                      'url': reverse('ticket_detail', args=[t.id]), 'icone': 'fas fa-ticket'})
    return itens


def _comunicados(user, termo):
    from communications.models import Communication

    qs = Communication.objects.all()
    if getattr(user, 'hierarchy', '') != 'SUPERADMIN':
        agora = timezone.now()
        qs = (qs.filter(Q(send_to_all=True) | Q(recipients=user) | Q(sender=user))
              .filter(Q(active_from__isnull=True) | Q(active_from__lte=agora))
              .filter(Q(active_until__isnull=True) | Q(active_until__gte=agora)))
    return [{'titulo': c.title, 'sub': timezone.localtime(c.created_at).strftime('%d/%m/%Y'),
             'url': reverse('communications:communication_detail', args=[c.id]), 'icone': 'fas fa-bullhorn'}
            for c in qs.filter(Q(title__icontains=termo) | Q(message__icontains=termo)).distinct().order_by('-created_at')[:LIMITE]]


def _facilitador(user, termo):
    from facilitador.models import Recurso
    from facilitador.views import pode_gerenciar

    qs = Recurso.objects.all() if pode_gerenciar(user) else Recurso.objects.filter(ativo=True)
    return [{'titulo': r.titulo, 'sub': ('Link · ' if r.e_link else 'Arquivo · ') + (r.categoria or 'Facilitador'),
             'url': reverse('facilitador:detalhe', args=[r.id]), 'icone': f'fas {r.icone}'}
            for r in qs.filter(Q(titulo__icontains=termo) | Q(descricao__icontains=termo) | Q(categoria__icontains=termo))[:LIMITE]]


def _pessoas(user, termo):
    if not user.can_manage_users():
        return []
    from users.models import User

    filtro = Q(email__icontains=termo) | Q(login_code__icontains=termo) | Q(job_title__icontains=termo)
    partes = termo.split()
    nome = Q()
    for p in partes:
        nome &= Q(first_name__icontains=p) | Q(last_name__icontains=p)
    return [{'titulo': u.get_full_name() or u.username,
             'sub': ' · '.join(x for x in (u.job_title, u.sector.name if u.sector_id else '', '' if u.is_active else 'inativo') if x),
             'url': reverse('edit_user', args=[u.id]), 'icone': 'fas fa-user'}
            for u in User.objects.filter(filtro | nome).select_related('sector').order_by('-is_active', 'first_name')[:LIMITE]]


GRUPOS = [('Chamados', _chamados), ('Comunicados', _comunicados), ('Facilitador', _facilitador), ('Pessoas', _pessoas)]


@login_required
def busca_global(request):
    termo = ' '.join((request.GET.get('q') or '').split())[:80]
    if len(termo) < 2:
        return JsonResponse({'grupos': []})
    grupos = []
    for titulo, fonte in GRUPOS:
        try:
            itens = fonte(request.user, termo)
        except Exception:  # noqa: BLE001 — um módulo com problema não derruba a busca inteira
            itens = []
        if itens:
            grupos.append({'titulo': titulo, 'itens': itens})
    return JsonResponse({'grupos': grupos})
