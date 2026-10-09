"""Log de alterações dos ativos legado (/assets/legado/).

Toda porta que muda um ativo passa por aqui — a tela de edição, a edição em
massa, a importação de planilha, o admin e a exclusão. O jeito de usar é o
mesmo em todas: tirar a ``foto`` do ativo antes, mexer, e chamar ``registrar``
com a foto de antes; o que mudou é calculado campo a campo.
"""
from .models import Asset, AssetHistorico

# (campo, rótulo) na ordem em que aparecem na tela.
CAMPOS = [
    ('patrimonio_numero', 'N° Patrimônio'),
    ('nome', 'Nome'),
    ('imei_serial', 'IMEI/Serial'),
    ('categoria', 'Categoria'),
    ('localizado', 'Localizado'),
    ('setor', 'Setor'),
    ('pdv', 'PDV'),
    ('estado_fisico', 'Estado físico'),
    ('observacoes', 'Observações'),
    ('photo', 'Foto'),
]
ROTULOS = dict(CAMPOS)


def _texto(asset, campo):
    valor = getattr(asset, campo, None)
    if campo == 'photo':
        return valor.name.rsplit('/', 1)[-1] if valor else ''
    exibir = getattr(asset, f'get_{campo}_display', None)
    if exibir and valor not in (None, ''):
        return str(exibir())
    return '' if valor is None else str(valor).strip()


def foto(asset):
    """Os valores do ativo agora, como texto — para comparar depois."""
    return {campo: _texto(asset, campo) for campo, _ in CAMPOS}


def diferencas(antes, depois):
    return [{'campo': campo, 'rotulo': ROTULOS[campo], 'antes': antes.get(campo, ''), 'depois': depois.get(campo, '')}
            for campo, _ in CAMPOS if antes.get(campo, '') != depois.get(campo, '')]


def _nome(usuario):
    if not usuario:
        return ''
    nome = (getattr(usuario, 'get_full_name', lambda: '')() or '').strip()
    return nome or getattr(usuario, 'username', '') or str(usuario)


def registrar(asset, usuario, acao, antes=None, origem='tela'):
    """Grava uma linha no log. Edição sem mudança nenhuma não grava nada.

    - criado: todas as informações entram como "depois";
    - editado: só o que mudou entre ``antes`` e o ativo agora;
    - excluido: o que o ativo tinha, como "antes" (chamar antes do delete()).
    """
    agora = foto(asset)
    if acao == 'criado':
        mudancas = diferencas({}, agora)
    elif acao == 'excluido':
        mudancas = diferencas(agora, {})
    else:
        mudancas = diferencas(antes or {}, agora)
        if not mudancas:
            return None
    return AssetHistorico.objects.create(
        asset=None if acao == 'excluido' else asset, patrimonio_numero=asset.patrimonio_numero[:20],
        acao=acao, origem=origem, usuario=usuario if getattr(usuario, 'pk', None) else None,
        usuario_nome=_nome(usuario)[:200], mudancas=mudancas)


def registrar_varios(ativos_antes, usuario, origem):
    """Depois de um update() em lote: compara cada ativo com a foto de antes.

    ``ativos_antes`` é {id: foto}. Lê os ativos de novo do banco.
    """
    linhas = []
    for asset in Asset.objects.filter(pk__in=list(ativos_antes)):
        mudancas = diferencas(ativos_antes[asset.pk], foto(asset))
        if mudancas:
            linhas.append(AssetHistorico(asset=asset, patrimonio_numero=asset.patrimonio_numero[:20], acao='editado',
                                         origem=origem, usuario=usuario, usuario_nome=_nome(usuario)[:200],
                                         mudancas=mudancas))
    AssetHistorico.objects.bulk_create(linhas, batch_size=500)
    return len(linhas)
