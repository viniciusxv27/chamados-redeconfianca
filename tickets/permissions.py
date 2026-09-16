"""Quem enxerga qual chamado quando a pessoa é o PADRÃO restrito.

Quem é "PADRÃO restrito" é decidido num lugar só, em
``users.module_access.padrao_restrito`` (PADRÃO, fora do grupo GERENTES, sem
liberação individual). Aqui fica o que isso quer dizer para os chamados:

- vê os chamados que abriu;
- vê aqueles em que é o responsável (``assigned_to``) — há PADRÃO responsável
  por chamado, e sem isso ele não consegue trabalhar;
- vê aqueles em que está como auxiliar ou em cópia (``TicketAssignment``), como
  já via;
- vê os chamados dos setores de atendimento em que trabalha — setor que não é
  loja, como a Manutenção —, como já via.

O que sai é o chamado da LOJA dele aberto ou atendido por outra pessoa. Para
todo o resto (outras hierarquias, GERENTES, liberação, superusuário) as funções
daqui devolvem exatamente a regra que as telas já tinham.
"""
from django.db.models import Q
from rest_framework.permissions import BasePermission


def chamados_restritos(user):
    """O PADRÃO restrito, na pergunta dos chamados."""
    from users.module_access import padrao_restrito
    return padrao_restrito(user, 'chamados')


def setores_do_usuario(user):
    """Ids do setor principal e dos setores adicionais."""
    ids = set(user.sectors.values_list('id', flat=True))
    if user.sector_id:
        ids.add(user.sector_id)
    return ids


def setores_pelo_setor(user):
    """Setores cujos chamados a pessoa enxerga só por ser do setor.

    Para quem não é restrito, todos os setores dela — como sempre foi. Para o
    PADRÃO restrito, só os setores de atendimento: tira as lojas (a mesma
    definição de loja da Contagem de Caixa, nome "Loja…" ou código ADABAS). O
    chamado da loja é da gerência da loja; o da Manutenção é o trabalho de
    quem está na Manutenção, e esse continua na fila de quem já o via.
    """
    ids = setores_do_usuario(user)
    if ids and chamados_restritos(user):
        from contagem_caixa.permissions import lojas
        ids -= set(lojas().filter(id__in=ids).values_list('id', flat=True))
    return ids


def filtro_do_padrao_restrito(user):
    """Q dos chamados que o PADRÃO restrito enxerga (lista, histórico, exportação, API)."""
    return (Q(created_by=user)
            | Q(assigned_to=user)
            | Q(additional_assignments__user=user, additional_assignments__is_active=True)
            | Q(sector_id__in=setores_pelo_setor(user)))


def pode_ver_chamado(user, ticket):
    """A pergunta da tela de detalhe, com o setor já recortado para o restrito."""
    if user.can_view_all_tickets():
        return True
    if user.can_view_sector_tickets() and ticket.sector_id in setores_pelo_setor(user):
        return True
    return ticket.created_by_id == user.id or user in ticket.get_all_assigned_users()


class ForaDoPadraoRestrito(BasePermission):
    """API de chamados fechada para o PADRÃO restrito.

    Serve para o que não é filtrável por chamado — o webhook, que entrega todo
    chamado novo de qualquer setor para a URL cadastrada.
    """
    message = 'Esta API de chamados não está liberada para o seu usuário.'

    def has_permission(self, request, view):
        return not chamados_restritos(request.user)
