"""Motor de permissões do Drive — a validação REAL, no servidor (RNF01/02/05).

As telas apenas escondem botões; quem decide é aqui, chamado por TODAS as views
antes de qualquer leitura/escrita. Contra URL direta (RNF05), o acesso a um
arquivo por id resolve a cadeia de pastas até um setor autorizado — quem não
tem o setor na cadeia não passa, mesmo adivinhando o id.

Níveis, em escada (cada um inclui os de baixo):
    VISUALIZAR < DOWNLOAD < UPLOAD < EDITAR < EXCLUIR < ADMINISTRAR
"""
from . import gdrive
from .models import SectorDriveMapping

ORDEM = {'VIEW': 1, 'DOWNLOAD': 2, 'UPLOAD': 3, 'EDIT': 4, 'DELETE': 5, 'ADMIN': 6}
ADMIN = 6

# Ação → nível mínimo exigido.
REQUERIDO = {
    'view': 'VIEW', 'download': 'DOWNLOAD', 'upload': 'UPLOAD', 'mkdir': 'UPLOAD',
    'rename': 'EDIT', 'move': 'EDIT', 'version': 'EDIT',
    'delete': 'DELETE', 'restore': 'DELETE', 'admin': 'ADMIN',
}


def is_superadmin(user):
    return bool(user and user.is_authenticated
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def _perms_do_usuario(user, mapping):
    """Permissões do setor que se aplicam a ``user`` (por alvo)."""
    grupos = set(user.communication_groups.values_list('id', flat=True))
    setores = set(user.sectors.values_list('id', flat=True))
    if getattr(user, 'sector_id', None):
        setores.add(user.sector_id)
    hier = getattr(user, 'hierarchy', '')

    out = []
    for p in mapping.permissoes.all():
        if p.alvo == 'USER' and p.target_user_id == user.id:
            out.append(p)
        elif p.alvo == 'GROUP' and p.target_group_id in grupos:
            out.append(p)
        elif p.alvo == 'SECTOR' and p.target_sector_id in setores:
            out.append(p)
        elif p.alvo == 'HIERARCHY' and p.target_hierarchy and p.target_hierarchy == hier:
            out.append(p)
    return out


def _nivel_max(perms):
    return max((ORDEM[p.nivel] for p in perms), default=0)


def _e_gestor(user, mapping):
    return any(m.id == user.id for m in mapping.managers.all())


def sectors_visible(user):
    """Mapeamentos de setor que ``user`` enxerga (para os cartões e a navegação)."""
    base = (SectorDriveMapping.objects.filter(ativo=True)
            .select_related('sector').prefetch_related('managers', 'permissoes'))
    if is_superadmin(user):
        return list(base)
    return [m for m in base if _e_gestor(user, m) or _perms_do_usuario(user, m)]


def mapping_por_setor(sector_id):
    return (SectorDriveMapping.objects.filter(ativo=True, sector_id=sector_id)
            .select_related('sector').prefetch_related('managers', 'permissoes').first())


def level_for_folder(user, mapping, folder_id=None):
    """Maior nível de ``user`` para uma pasta específica do setor (0 = nenhum)."""
    if is_superadmin(user) or _e_gestor(user, mapping):
        return ADMIN
    perms = _perms_do_usuario(user, mapping)
    nivel = _nivel_max([p for p in perms if not p.folder_id])   # valem no setor todo
    escopadas = [p for p in perms if p.folder_id]
    if escopadas and folder_id:
        cadeia = set(gdrive.ancestrais(folder_id)) | {folder_id}
        nivel = max(nivel, _nivel_max([p for p in escopadas if p.folder_id in cadeia]))
    return nivel


def can(user, mapping, action, folder_id=None):
    """A ação é permitida para ``user`` naquela pasta do setor?"""
    if mapping is None:
        return False
    return level_for_folder(user, mapping, folder_id) >= ORDEM[REQUERIDO[action]]


def file_allowed(user, file_id):
    """(mapping, nivel) do arquivo, ou (None, 0). O portão contra URL direta.

    Resolve a cadeia de pastas do arquivo UMA vez e procura, entre os setores
    que o usuário enxerga, um cujo folder-raiz esteja nessa cadeia.
    """
    if not file_id:
        return None, 0
    cadeia = set(gdrive.ancestrais(file_id))
    cadeia.add(file_id)
    melhor = (None, 0)
    for m in sectors_visible(user):
        if m.folder_id not in cadeia:
            continue
        if is_superadmin(user) or _e_gestor(user, m):
            return m, ADMIN
        perms = _perms_do_usuario(user, m)
        nivel = _nivel_max([p for p in perms if not p.folder_id])
        nivel = max(nivel, _nivel_max([p for p in perms if p.folder_id and p.folder_id in cadeia]))
        if nivel > melhor[1]:
            melhor = (m, nivel)
    return melhor


def folder_allowed(user, folder_id, mapping=None):
    """Como file_allowed, mas para uma PASTA (navegação). (mapping, nivel)."""
    return file_allowed(user, folder_id)


# ─── Quem tem acesso (tela do SUPERADMIN) ────────────────────────────────────

def usuarios_com_acesso():
    """{user: {'setores': set(nomes), 'gestor_de': set(nomes)}} — para a lista.

    Resolve cada alvo (usuário/grupo/setor/hierarquia) em usuários concretos.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    resultado = {}

    def _add(u, setor_nome, gestor=False):
        item = resultado.setdefault(u, {'setores': set(), 'gestor_de': set()})
        item['setores'].add(setor_nome)
        if gestor:
            item['gestor_de'].add(setor_nome)

    mappings = (SectorDriveMapping.objects.filter(ativo=True)
                .select_related('sector').prefetch_related('managers', 'permissoes'))
    for m in mappings:
        nome = m.sector.name
        for g in m.managers.all():
            _add(g, nome, gestor=True)
        for p in m.permissoes.all():
            if p.alvo == 'USER' and p.target_user_id:
                if p.target_user:
                    _add(p.target_user, nome)
            elif p.alvo == 'GROUP' and p.target_group_id:
                for u in User.objects.filter(communication_groups__id=p.target_group_id, is_active=True):
                    _add(u, nome)
            elif p.alvo == 'SECTOR' and p.target_sector_id:
                for u in User.objects.filter(is_active=True).filter(
                        models_q_setor(p.target_sector_id)):
                    _add(u, nome)
            elif p.alvo == 'HIERARCHY' and p.target_hierarchy:
                for u in User.objects.filter(is_active=True, hierarchy=p.target_hierarchy):
                    _add(u, nome)
    return resultado


def models_q_setor(sector_id):
    """Q que casa usuários do setor (principal ou M2M)."""
    from django.db.models import Q
    return Q(sector_id=sector_id) | Q(sectors__id=sector_id)
