"""Motor de permissões do Drive — a validação REAL, no servidor (RNF01/02/05).

As telas apenas escondem botões; quem decide é aqui, chamado por TODAS as views
antes de qualquer leitura/escrita. Contra URL direta (RNF05), o acesso a um
arquivo por id resolve a cadeia de pastas até um setor autorizado — quem não
tem o setor na cadeia não passa, mesmo adivinhando o id.

Níveis, em escada (cada um inclui os de baixo):
    VISUALIZAR < DOWNLOAD < UPLOAD < EDITAR < EXCLUIR < ADMINISTRAR
"""
from django.db import DatabaseError

from . import gdrive
from .models import PastaLiberada, SectorDriveMapping

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


def _de_quem(user):
    """(grupos, setores, hierarquia) do usuário — o que os alvos comparam."""
    grupos = set(user.communication_groups.values_list('id', flat=True))
    setores = set(user.sectors.values_list('id', flat=True))
    if getattr(user, 'sector_id', None):
        setores.add(user.sector_id)
    return grupos, setores, getattr(user, 'hierarchy', '')


def alvo_aplica(p, user, de_quem=None):
    """A permissão (ou pasta liberada) é dirigida a ``user``?

    Mesma regra para os dois: o alvo pode ser a pessoa, um grupo, um setor ou
    uma hierarquia. Fica numa função só porque uma pasta liberada é a mesma
    pergunta feita fora do mapa de setores.
    """
    grupos, setores, hier = de_quem or _de_quem(user)
    if p.alvo == 'USER':
        return p.target_user_id == user.id
    if p.alvo == 'GROUP':
        return p.target_group_id in grupos
    if p.alvo == 'SECTOR':
        return p.target_sector_id in setores
    if p.alvo == 'HIERARCHY':
        return bool(p.target_hierarchy) and p.target_hierarchy == hier
    return False


def _perms_do_usuario(user, mapping):
    """Permissões do setor que se aplicam a ``user`` (por alvo)."""
    de_quem = _de_quem(user)
    return [p for p in mapping.permissoes.all() if alvo_aplica(p, user, de_quem)]


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


def pastas_liberadas(user):
    """Pastas liberadas direto para ``user`` — cada uma é uma raiz em /drive.

    O SUPERADMIN vê todas: é ele quem libera, e precisa conseguir abrir o que
    entregou para conferir.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return []
    base = (PastaLiberada.objects.filter(ativo=True)
            .select_related('target_user', 'target_group', 'target_sector'))
    try:
        liberadas = list(base)
    except DatabaseError:
        # Servidor que ainda não rodou o migrate: sem a tabela, não há pasta
        # liberada — o Drive segue funcionando pelo mapa de setores.
        return []
    if is_superadmin(user):
        return liberadas
    de_quem = _de_quem(user)
    return [p for p in liberadas if alvo_aplica(p, user, de_quem)]


def pasta_liberada_por_id(pasta_id):
    return PastaLiberada.objects.filter(ativo=True, pk=pasta_id).first()


def nivel_na_pasta(user, pasta):
    """Nível de ``user`` numa pasta liberada (0 = nenhum).

    A liberação vale para a pasta e tudo abaixo dela — não há recorte por
    subpasta aqui: quem precisa de recorte libera a subpasta.
    """
    if pasta is None:
        return 0
    if is_superadmin(user):
        return ADMIN
    if not pasta.ativo or not alvo_aplica(pasta, user):
        return 0
    return ORDEM.get(pasta.nivel, 0)


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


def file_allowed(user, file_id, cadeia=None, mapeamentos=None, liberadas=None):
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

    # Pastas liberadas direto: são raízes como as do setor, só que sem setor.
    # Ficam depois no desempate para o arquivo que está nos dois lugares
    # continuar sendo tratado como do setor (auditoria, notificação, trilha).
    for pasta in (pastas_liberadas(user) if liberadas is None else liberadas):
        if pasta.folder_id not in cadeia:
            continue
        nivel = ADMIN if is_superadmin(user) else ORDEM.get(pasta.nivel, 0)
        if nivel > melhor[1]:
            melhor = (pasta, nivel)
    return melhor


CACHE_PAI_SEGUNDOS = 300
PROFUNDIDADE_MAXIMA = 30


def resolver_acessos(user, arquivos, mapeamentos=None, pais=None, liberadas=None):
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
    mapeamentos = sectors_visible(user) if mapeamentos is None else mapeamentos
    liberadas = pastas_liberadas(user) if liberadas is None else liberadas
    pais = {} if pais is None else pais
    for f in arquivos:
        if f.get('id'):
            pais.setdefault(f['id'], (f.get('parents') or [''])[0])

    # Subir além da raiz do setor não serve para nada: a permissão é decidida
    # quando a raiz aparece na cadeia.
    raizes = {m.folder_id for m in mapeamentos if m.folder_id}
    raizes |= {p.folder_id for p in liberadas if p.folder_id}
    preencher_pais([f.get('id') for f in arquivos], pais, parar_em=raizes)

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
            atual = pais.get(atual)
        resultado[fid] = file_allowed(user, fid, cadeia=cadeia, mapeamentos=mapeamentos,
                                      liberadas=liberadas)
    return resultado


def preencher_pais(ids, pais, parar_em=()):
    """Sobe a árvore de TODOS os itens ao mesmo tempo: um pedido por nível.

    Antes cada item subia sozinho, uma chamada ao Google por degrau — 80
    resultados de busca davam ~80 idas e voltas de 250 ms, quase vinte
    segundos só nisso. Aqui cada degrau é um lote só (`gdrive.pais_de`), então
    a subida inteira custa um ou dois segundos mesmo com o cache frio.

    O cache de pai continua valendo (pasta raramente muda de lugar) e a falha
    do Google continua não sendo guardada: esconderia a pasta por minutos.
    """
    from django.core.cache import cache

    nivel = {i for i in ids if i}
    for _ in range(PROFUNDIDADE_MAXIMA):
        faltam = [i for i in nivel if i not in pais]
        if faltam:
            guardados = {}
            try:
                guardados = cache.get_many([f'drive:pai:{i}' for i in faltam]) or {}
            except Exception:  # noqa: BLE001 — sem Redis, só fica sem o atalho
                guardados = {}
            for item_id in faltam:
                chave = f'drive:pai:{item_id}'
                if chave in guardados:
                    pais[item_id] = guardados[chave]
            faltam = [i for i in faltam if i not in pais]
        if faltam:
            try:
                achados = gdrive.pais_de(faltam)
            except gdrive.DriveError:
                achados = {}
            novos = {}
            for item_id in faltam:
                if item_id in achados:
                    pais[item_id] = achados[item_id]
                    novos[f'drive:pai:{item_id}'] = achados[item_id]
                else:
                    pais[item_id] = None          # o Google não respondeu por ele
            if novos:
                try:
                    cache.set_many(novos, CACHE_PAI_SEGUNDOS)
                except Exception:  # noqa: BLE001
                    pass
        # Chegou numa raiz: dali para cima não muda mais nada.
        nivel = {pais.get(i) for i in nivel if i not in parar_em}
        nivel.discard(None)
        nivel.discard('')
        nivel -= set(parar_em)
        if not nivel:
            break
    return pais


def folder_allowed(user, folder_id, mapping=None):
    """Como file_allowed, mas para uma PASTA (navegação). (mapping, nivel)."""
    return file_allowed(user, folder_id)


# ─── Quem tem acesso (tela do SUPERADMIN) ────────────────────────────────────

def usuarios_com_acesso():
    """{user: {'setores', 'gestor_de', 'pastas'}} — para a lista de acessos.

    Resolve cada alvo (usuário/grupo/setor/hierarquia) em usuários concretos —
    tanto as permissões de setor quanto as pastas liberadas soltas, que também
    são acesso e não podem ficar de fora da conferência.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    resultado = {}

    def _add(u, setor_nome, gestor=False, pasta=False):
        item = resultado.setdefault(u, {'setores': set(), 'gestor_de': set(), 'pastas': set()})
        if pasta:
            item['pastas'].add(setor_nome)
            return
        item['setores'].add(setor_nome)
        if gestor:
            item['gestor_de'].add(setor_nome)

    def _espalhar(alvo, quem_id, hierarquia, nome, pasta=False):
        """Aplica um alvo a todos os usuários que ele alcança."""
        if alvo == 'USER' and quem_id:
            u = User.objects.filter(pk=quem_id).first()
            if u:
                _add(u, nome, pasta=pasta)
        elif alvo == 'GROUP' and quem_id:
            for u in User.objects.filter(communication_groups__id=quem_id, is_active=True):
                _add(u, nome, pasta=pasta)
        elif alvo == 'SECTOR' and quem_id:
            for u in User.objects.filter(is_active=True).filter(models_q_setor(quem_id)):
                _add(u, nome, pasta=pasta)
        elif alvo == 'HIERARCHY' and hierarquia:
            for u in User.objects.filter(is_active=True, hierarchy=hierarquia):
                _add(u, nome, pasta=pasta)

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

    for liberada in (PastaLiberada.objects.filter(ativo=True)
                     .select_related('target_user', 'target_group', 'target_sector')):
        quem_id = (liberada.target_user_id or liberada.target_group_id
                   or liberada.target_sector_id)
        _espalhar(liberada.alvo, quem_id, liberada.target_hierarchy, liberada.rotulo, pasta=True)
    return resultado


def models_q_setor(sector_id):
    """Q que casa usuários do setor (principal ou M2M)."""
    from django.db.models import Q
    return Q(sector_id=sector_id) | Q(sectors__id=sector_id)
