"""Quem vê, quem gerencia, a lixeira e o log de /files/ — num lugar só.

- **Ver**: o SUPERADMIN vê tudo. Os demais veem o arquivo quando ele foi liberado
  para eles por pessoa, ou quando a regra do arquivo (todos, setor, grupo, usuário)
  vale para eles **e** todas as pastas acima dele estão visíveis para eles.
- **Gerenciar** (criar pasta/categoria, mover, excluir qualquer item): quem pode
  enviar arquivos (ADMINISTRATIVO e acima, ou a liberação "arquivos.enviar") e o
  SUPERADMIN. Quem enviou um arquivo pode mandá-lo para a lixeira.
- **Excluir nunca apaga**: o item vai para a lixeira (com quem, quando e o lote) e
  o arquivo continua no storage. Só o SUPERADMIN vê a lixeira e recupera.
- **Log**: cada movimentação vira uma ``MovimentacaoArquivo``.
"""
import uuid

from django.db import transaction
from django.utils import timezone

from .models import FileCategory, Folder, MovimentacaoArquivo, SharedFile

Acao = MovimentacaoArquivo.Acao
Tipo = MovimentacaoArquivo.Tipo


# ---------------------------------------------------------------------------
# Quem pode o quê
# ---------------------------------------------------------------------------
def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def pode_gerir(user):
    """Criar pasta e categoria, mover e excluir qualquer item."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    return e_superadmin(user) or bool(user.can_upload_files())


def pode_excluir_arquivo(user, arquivo):
    return pode_gerir(user) or (getattr(user, 'pk', None) and arquivo.uploaded_by_id == user.pk)


def ip_de(request):
    encaminhado = request.META.get('HTTP_X_FORWARDED_FOR')
    return (encaminhado.split(',')[0].strip() if encaminhado else request.META.get('REMOTE_ADDR')) or None


# ---------------------------------------------------------------------------
# O que está fora da lixeira
# ---------------------------------------------------------------------------
def pastas():
    return Folder.objects.filter(excluido_em__isnull=True)


def categorias():
    return FileCategory.objects.filter(excluido_em__isnull=True)


def arquivos():
    return SharedFile.objects.filter(excluido_em__isnull=True)


# ---------------------------------------------------------------------------
# Visibilidade
# ---------------------------------------------------------------------------
class Visao:
    """O que uma pessoa enxerga, calculado uma vez por requisição.

    São poucas dezenas de pastas e arquivos: carregar tudo e decidir em memória
    custa menos que uma consulta por item (e a regra fica num lugar só).
    """

    def __init__(self, user):
        self.user = user
        self.tudo = e_superadmin(user)
        self._pastas = {p.pk: p for p in pastas().select_related('target_sector')}
        uid = getattr(user, 'pk', None)
        self._pastas_liberadas = set(Folder.allowed_users.through.objects.filter(user_id=uid)
                                     .values_list('folder_id', flat=True)) if uid else set()
        self._arquivos_liberados = set(SharedFile.allowed_users.through.objects.filter(user_id=uid)
                                       .values_list('sharedfile_id', flat=True)) if uid else set()
        self._setores = None
        self._grupos = None
        self._cache_pasta = {}

    # -- regras simples, sem consulta por item --
    def _no_setor(self, setor_id):
        if self._setores is None:
            ids = set()
            if getattr(self.user, 'sector_id', None):
                ids.add(self.user.sector_id)
            try:
                ids |= set(self.user.sectors.values_list('id', flat=True))
            except Exception:                                   # noqa: BLE001
                pass
            self._setores = ids
        return setor_id in self._setores

    def _no_grupo(self, grupo_id):
        if self._grupos is None:
            try:
                self._grupos = set(self.user.communication_groups.values_list('id', flat=True))
            except Exception:                                   # noqa: BLE001
                self._grupos = set()
        return grupo_id in self._grupos

    def _regra_da_pasta(self, pasta):
        if not pasta.is_active:
            return False
        if pasta.pk in self._pastas_liberadas:
            return True
        if pasta.visibility == 'ALL':
            return True
        if pasta.visibility == 'SECTOR' and pasta.target_sector_id:
            return self._no_setor(pasta.target_sector_id)
        if pasta.visibility == 'ADMIN':
            return bool(self.user.can_access_admin_panel())
        return False

    # -- perguntas da tela --
    def pasta(self, pasta_ou_id):
        """A pasta e todas as de cima estão visíveis (e fora da lixeira)?"""
        if pasta_ou_id is None:
            return True
        pid = getattr(pasta_ou_id, 'pk', pasta_ou_id)
        if pid in self._cache_pasta:
            return self._cache_pasta[pid]
        pasta = self._pastas.get(pid)
        if pasta is None:                                       # na lixeira ou inexistente
            resultado = False
        elif self.tudo:
            resultado = True
        else:
            resultado = self._regra_da_pasta(pasta) and self.pasta(pasta.parent_id)
        self._cache_pasta[pid] = resultado
        return resultado

    def categoria(self, categoria):
        if categoria is None or categoria.excluido_em is not None:
            return False
        if self.tudo:
            return True
        return categoria.is_active and self.pasta(categoria.folder_id)

    def arquivo(self, arquivo):
        if arquivo.excluido_em is not None:
            return False
        if self.tudo:
            return True
        if not arquivo.is_active:
            return False
        if arquivo.pk in self._arquivos_liberados:
            return True
        categoria = arquivo.category
        if categoria.excluido_em is not None or not self.categoria(categoria):
            return False
        regra = arquivo.visibility
        if regra == 'ALL':
            return True
        if regra == 'SECTOR' and arquivo.target_sector_id:
            return self._no_setor(arquivo.target_sector_id)
        if regra == 'GROUP' and arquivo.target_group_id:
            return self._no_grupo(arquivo.target_group_id)
        if regra == 'USER' and arquivo.target_user_id:
            return arquivo.target_user_id == getattr(self.user, 'pk', None)
        return False


def arquivos_visiveis(user, base=None):
    visao = Visao(user)
    base = base if base is not None else arquivos()
    return [a for a in base.select_related('category', 'uploaded_by') if visao.arquivo(a)]


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------
def registrar(acao, item, usuario=None, request=None, detalhe='', pessoa=None):
    if isinstance(item, SharedFile):
        tipo, nome = Tipo.ARQUIVO, item.title
    elif isinstance(item, Folder):
        tipo, nome = Tipo.PASTA, item.get_full_path() if item.pk else item.name
    else:
        tipo, nome = Tipo.CATEGORIA, item.name
    if usuario is None and request is not None:
        usuario = request.user
    return MovimentacaoArquivo.objects.create(
        acao=acao, tipo=tipo, item_id=item.pk, item_nome=(nome or '')[:255],
        usuario=usuario if getattr(usuario, 'pk', None) else None,
        pessoa=pessoa, detalhe=(detalhe or '')[:4000],
        ip=ip_de(request) if request is not None else None)


# ---------------------------------------------------------------------------
# Lixeira
# ---------------------------------------------------------------------------
def _descendentes(pasta):
    """Subpastas (todas as gerações) que ainda não estão na lixeira."""
    resultado, fila = [], [pasta.pk]
    while fila:
        filhas = list(pastas().filter(parent_id__in=fila))
        resultado += filhas
        fila = [f.pk for f in filhas]
    return resultado


@transaction.atomic
def mandar_para_lixeira(item, usuario, request=None):
    """Tira o item das telas (e o que está dentro dele), sem apagar nada.

    Devolve quantos itens foram juntos, por tipo.
    """
    agora = timezone.now()
    lote = uuid.uuid4().hex
    marca = {'excluido_em': agora, 'excluido_por': usuario if getattr(usuario, 'pk', None) else None,
             'lote_exclusao': lote}
    contagem = {'pastas': 0, 'categorias': 0, 'arquivos': 0}
    if isinstance(item, SharedFile):
        contagem['arquivos'] = arquivos().filter(pk=item.pk).update(**marca)
        detalhe = f'Categoria "{item.category.name}"'
    elif isinstance(item, FileCategory):
        contagem['arquivos'] = arquivos().filter(category=item).update(**marca)
        contagem['categorias'] = categorias().filter(pk=item.pk).update(**marca)
        detalhe = f'Com {contagem["arquivos"]} arquivo(s)'
    else:
        todas = [item] + _descendentes(item)
        ids = [p.pk for p in todas]
        contagem['arquivos'] = arquivos().filter(category__folder_id__in=ids).update(**marca)
        contagem['categorias'] = categorias().filter(folder_id__in=ids).update(**marca)
        contagem['pastas'] = pastas().filter(pk__in=ids).update(**marca)
        detalhe = (f'Com {contagem["pastas"] - 1} subpasta(s), {contagem["categorias"]} categoria(s) '
                   f'e {contagem["arquivos"]} arquivo(s)')
    registrar(Acao.EXCLUIR, item, usuario, request, detalhe)
    return contagem


def _lote_de(item):
    return item.lote_exclusao


@transaction.atomic
def restaurar(item, usuario, request=None):
    """Volta o item — e tudo o que saiu junto com ele no mesmo lote.

    Se o lugar onde ele estava também está na lixeira (por outra exclusão), o
    caminho até ele volta junto, senão o item voltaria para um lugar invisível.
    """
    lote = _lote_de(item)
    limpar = {'excluido_em': None, 'excluido_por': None, 'lote_exclusao': ''}
    voltaram = {'pastas': 0, 'categorias': 0, 'arquivos': 0}
    if lote:
        voltaram['pastas'] = Folder.objects.filter(lote_exclusao=lote).update(**limpar)
        voltaram['categorias'] = FileCategory.objects.filter(lote_exclusao=lote).update(**limpar)
        voltaram['arquivos'] = SharedFile.objects.filter(lote_exclusao=lote).update(**limpar)
    else:
        type(item).objects.filter(pk=item.pk).update(**limpar)

    # O caminho até o item.
    item.refresh_from_db()
    if isinstance(item, SharedFile):
        categoria = item.category
        if categoria.excluido_em is not None:
            FileCategory.objects.filter(pk=categoria.pk).update(**limpar)
            voltaram['categorias'] += 1
        pasta = categoria.folder
    elif isinstance(item, FileCategory):
        pasta = item.folder
    else:
        pasta = item.parent
    while pasta is not None:
        if pasta.excluido_em is not None:
            Folder.objects.filter(pk=pasta.pk).update(**limpar)
            voltaram['pastas'] += 1
        pasta = pasta.parent

    partes = [f'{n} {nome}' for nome, n in voltaram.items() if n]
    registrar(Acao.RESTAURAR, item, usuario, request, 'Voltaram: ' + (', '.join(partes) or 'o item'))
    return voltaram


# ---------------------------------------------------------------------------
# Acesso por pessoa
# ---------------------------------------------------------------------------
def liberar(item, pessoas, usuario, request=None):
    novas = [p for p in pessoas if not item.allowed_users.filter(pk=p.pk).exists()]
    if novas:
        item.allowed_users.add(*novas)
    for pessoa in novas:
        registrar(Acao.LIBERAR, item, usuario, request, f'Para {pessoa.full_name}', pessoa=pessoa)
    return len(novas)


def retirar(item, pessoa, usuario, request=None):
    if not item.allowed_users.filter(pk=pessoa.pk).exists():
        return False
    item.allowed_users.remove(pessoa)
    registrar(Acao.RETIRAR, item, usuario, request, f'De {pessoa.full_name}', pessoa=pessoa)
    return True
