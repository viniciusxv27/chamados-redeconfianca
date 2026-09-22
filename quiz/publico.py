"""De onde saem os participantes de uma sala: pessoa, loja, cargo, setor, grupo ou coordenação.

Reaproveita o catálogo das reuniões (``reunioes/publico.py``) para cargos e
grupos. Lojas, setores e coordenações saem do setor principal (``user.sector``):
o M2M ``sectors`` de quem é de escritório tem várias ou todas as lojas, e
"chamar a loja tal" puxaria essa gente em toda loja. As lojas vêm separadas dos
demais setores — é o jeito mais comum de montar um quiz. Quem cria a sala não
entra na lista (é o responsável, não joga).
"""
from django.contrib.auth import get_user_model

from reunioes import publico as base

from .models import Participante

CAMINHOS = ('lojas', 'setores', 'cargos', 'grupos', 'coordenacoes')
# Quando a mesma pessoa cai por mais de um caminho, vale o mais específico.
_ORIGEM = (
    ('coordenacoes', Participante.Origem.COORDENACAO),
    ('grupos', Participante.Origem.GRUPO),
    ('lojas', Participante.Origem.LOJA),
    ('setores', Participante.Origem.SETOR),
    ('cargos', Participante.Origem.CARGO),
)


def _e_loja(item):
    return 'loja' in item['nome'].lower()


def _setores_principais(usuario):
    """Cada setor com quem o tem como setor principal."""
    from users.models import Sector

    membros = {}
    for uid, setor_id in (get_user_model().objects.filter(is_active=True, sector__isnull=False)
                          .exclude(pk=usuario.pk).values_list('id', 'sector_id')):
        membros.setdefault(setor_id, []).append(uid)
    return [{'id': s.id, 'nome': s.name, 'membros': membros[s.id]}
            for s in Sector.objects.filter(id__in=list(membros)).order_by('name')]


def _coordenacoes(usuario, setores):
    """Cada coordenador com a gente das lojas da carteira dele (``CoordinatorStoreAccess``) — e ele junto."""
    from simulator.models import CoordinatorStoreAccess

    membros_do_setor = {s['id']: s['membros'] for s in setores}
    saida = []
    for acesso in CoordinatorStoreAccess.objects.select_related('coordinator').prefetch_related('sectors'):
        coord = acesso.coordinator
        if not (coord and coord.is_active):
            continue
        lojas = [s.id for s in acesso.sectors.all()]
        ids = {uid for setor_id in lojas for uid in membros_do_setor.get(setor_id, [])}
        if coord.pk != usuario.pk:
            ids.add(coord.pk)
        if lojas and ids:
            saida.append({'id': acesso.pk,
                          'nome': f'{coord.get_full_name() or coord.username} '
                                  f'({len(lojas)} loja{"s" if len(lojas) > 1 else ""})',
                          'membros': sorted(ids)})
    return sorted(saida, key=lambda x: x['nome'])


def catalogo(usuario):
    setores = _setores_principais(usuario)
    return {
        'lojas': [s for s in setores if _e_loja(s)],
        'setores': [s for s in setores if not _e_loja(s)],
        'cargos': base.cargos(usuario),
        'grupos': base.grupos(usuario),
        'coordenacoes': _coordenacoes(usuario, setores),
    }


def escolhidos_do_post(post):
    return {caminho: post.getlist(caminho) for caminho in CAMINHOS}


def expandir(catalogo_, escolhidos):
    ids = set()
    for caminho in CAMINHOS:
        marcados = {str(x) for x in escolhidos.get(caminho, [])}
        for item in catalogo_.get(caminho, []):
            if str(item['id']) in marcados:
                ids.update(item['membros'])
    return ids


def origens(catalogo_, escolhidos):
    """{user_id: (origem, rótulo)} — para o convite dizer por que a pessoa foi chamada."""
    saida = {}
    for caminho, origem in _ORIGEM:
        marcados = {str(x) for x in escolhidos.get(caminho, [])}
        for item in catalogo_.get(caminho, []):
            if str(item['id']) not in marcados:
                continue
            for uid in item['membros']:
                saida.setdefault(uid, (origem, item['nome']))
    return saida
