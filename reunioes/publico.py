"""De onde saem os convidados de uma reunião.

Quatro caminhos, porque é assim que a empresa pensa: "chama os gerentes"
(cargo), "chama a loja tal" (setor), "chama o grupo do RH" (grupo) ou "chama a
coordenação da Thayandra" (coordenação — o coordenador e as lojas dele).

Todos devolvem o mesmo formato para a tela: id, nome e a lista de ids que
aquele botão convida. Quem está criando a reunião nunca entra na lista: já é o
organizador, e contá-lo faria o "N pessoas" mentir.
"""
from django.contrib.auth import get_user_model

User = get_user_model()


def _ativos(qs, exceto):
    return [u.pk for u in qs.filter(is_active=True).exclude(pk=exceto.pk).distinct()]


def cargos(usuario):
    """Cargos (job_title) com pelo menos uma pessoa ativa."""
    mapa = {}
    for u in (User.objects.filter(is_active=True).exclude(job_title='')
              .exclude(pk=usuario.pk).only('id', 'job_title')):
        mapa.setdefault(u.job_title.strip(), []).append(u.pk)
    return [{'id': nome, 'nome': nome, 'membros': ids}
            for nome, ids in sorted(mapa.items()) if ids]


def setores(usuario):
    """Setores/lojas, contando tanto o setor principal quanto os secundários."""
    from django.db.models import Q
    from users.models import Sector

    saida = []
    for s in Sector.objects.order_by('name'):
        ids = _ativos(User.objects.filter(Q(sector_id=s.id) | Q(sectors__id=s.id)), usuario)
        if ids:
            saida.append({'id': s.id, 'nome': s.name, 'membros': ids})
    return saida


def grupos(usuario):
    """Grupos de comunicação (/users/manage/groups/)."""
    from communications.models import CommunicationGroup

    saida = []
    for g in (CommunicationGroup.objects.filter(is_active=True)
              .prefetch_related('members').order_by('name')):
        ids = [m.pk for m in g.members.all() if m.is_active and m.pk != usuario.pk]
        if ids:
            saida.append({'id': g.pk, 'nome': g.name, 'membros': ids})
    return saida


def coordenacoes(usuario):
    """Cada coordenador com a gente das lojas dele — e ele junto.

    A carteira do coordenador já está cadastrada no simulador
    (``CoordinatorStoreAccess``); reaproveitar evita uma segunda lista de lojas
    por coordenador para alguém manter à mão.
    """
    from django.db.models import Q

    from simulator.models import CoordinatorStoreAccess

    saida = []
    for acesso in (CoordinatorStoreAccess.objects
                   .select_related('coordinator').prefetch_related('sectors')):
        coord = acesso.coordinator
        if not (coord and coord.is_active):
            continue
        ids_setores = [s.id for s in acesso.sectors.all()]
        if not ids_setores:
            continue
        pessoas = User.objects.filter(
            Q(sector_id__in=ids_setores) | Q(sectors__id__in=ids_setores) | Q(pk=coord.pk))
        ids = _ativos(pessoas, usuario)
        if ids:
            saida.append({
                'id': acesso.pk,
                'nome': f'{coord.get_full_name() or coord.username} '
                        f'({len(ids_setores)} loja{"s" if len(ids_setores) > 1 else ""})',
                'membros': ids,
            })
    return sorted(saida, key=lambda x: x['nome'])


def tudo(usuario):
    """As quatro listas de uma vez, para a tela de criar reunião."""
    return {
        'cargos': cargos(usuario),
        'setores': setores(usuario),
        'grupos': grupos(usuario),
        'coordenacoes': coordenacoes(usuario),
    }


def origens(catalogo, escolhidos):
    """Por que cada pessoa foi convidada.

    ``escolhidos`` é o que veio da tela: {'cargos': [...], 'setores': [...], ...}.
    Quando a mesma pessoa cai por mais de um caminho, vale o mais específico —
    a coordenação explica melhor do que "pelo cargo".

    Serve para o convite dizer "você entrou como GERENTE DE VENDAS" em vez de a
    reunião aparecer do nada na agenda de alguém.
    """
    from .models import ParticipanteReuniao

    ordem = (
        ('coordenacoes', ParticipanteReuniao.COORDENACAO),
        ('grupos', ParticipanteReuniao.GRUPO),
        ('setores', ParticipanteReuniao.SETOR),
        ('cargos', ParticipanteReuniao.CARGO),
    )
    saida = {}
    for chave, tipo in ordem:
        marcados = {str(x) for x in escolhidos.get(chave, [])}
        for item in catalogo.get(chave, []):
            if str(item['id']) not in marcados:
                continue
            for uid in item['membros']:
                saida.setdefault(uid, (tipo, item['nome']))
    return saida


def expandir(catalogo, escolhidos):
    """Todos os ids que os caminhos marcados convidam."""
    ids = set()
    for chave in ('cargos', 'setores', 'grupos', 'coordenacoes'):
        marcados = {str(x) for x in escolhidos.get(chave, [])}
        for item in catalogo.get(chave, []):
            if str(item['id']) in marcados:
                ids.update(item['membros'])
    return ids
