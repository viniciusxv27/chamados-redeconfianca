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


def file_allowed(user, file_id, cadeia=None, mapeamentos=None):
    """(mapping, nivel) do arquivo, ou (None, 0). O portão contra URL direta.

    Resolve a cadeia de pastas do arquivo UMA vez e procura, entre os setores
    que o usuário enxerga, um cujo folder-raiz esteja nessa cadeia.

    `cadeia` e `mapeamentos` deixam quem checa muitos arquivos de uma vez (a
    lixeira) passar a subida da árvore e os setores já prontos.
    """
    if not file_id:
        return None, 0
    cadeia = set(gdrive.ancestrais(file_id) if cadeia is None else cadeia)
    cadeia.add(file_id)
    melhor = (None, 0)
    for m in (sectors_visible(user) if mapeamentos is None else mapeamentos):
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


CACHE_PAI_SEGUNDOS = 300
PROFUNDIDADE_MAXIMA = 30


def resolver_acessos(user, arquivos, mapeamentos=None, pais=None):
    """{id: (mapping, nivel)} de vários arquivos, subindo a árvore uma vez só.

    A lixeira checava item por item com `file_allowed`: cada arquivo subia a
    árvore inteira, uma chamada ao Google por nível, e ainda buscava os setores
    no banco de novo. Com 200 itens eram centenas de chamadas antes da tela
    aparecer. Aqui a subida é compartilhada: os `parents` que já vêm na
    listagem não custam nada, pasta já vista não é consultada de novo, e o pai
    de cada pasta fica alguns minutos no cache para o "mostrar mais".

    O cache serve SÓ para listar. Restaurar e excluir continuam checando com
    `file_allowed` sem cache: uma pasta movida direto no Google não pode dar
    acesso por causa de um pai guardado.
    """
    from django.core.cache import cache

    mapeamentos = sectors_visible(user) if mapeamentos is None else mapeamentos
    pais = {} if pais is None else pais
    for f in arquivos:
        if f.get('id'):
            pais.setdefault(f['id'], (f.get('parents') or [''])[0])

    def pai(item_id):
        if item_id in pais:
            return pais[item_id]
        chave = f'drive:pai:{item_id}'
        guardado = None
        try:
            guardado = cache.get(chave)
        except Exception:  # noqa: BLE001 — sem Redis, só fica sem o atalho
            pass
        if guardado is None:
            guardado = gdrive.pai_de(item_id)
            if guardado is not None:
                # Falha do Google (None) não é guardada: esconderia a pasta por minutos.
                try:
                    cache.set(chave, guardado, CACHE_PAI_SEGUNDOS)
                except Exception:  # noqa: BLE001
                    pass
        pais[item_id] = guardado
        return guardado

    resultado = {}
    for f in arquivos:
        fid = f.get('id')
        if not fid:
            continue
        cadeia, atual = [], fid
        for _ in range(PROFUNDIDADE_MAXIMA):
            if not atual or atual in cadeia:
                break
            cadeia.append(atual)
            atual = pai(atual)
        resultado[fid] = file_allowed(user, fid, cadeia=cadeia, mapeamentos=mapeamentos)
    return resultado


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
