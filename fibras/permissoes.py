"""Acesso ao módulo de Fibras (27/10/2026).

Só o SUPERADMIN entra de saída; ele libera, pessoa por pessoa, cada parte do
módulo (``FUNCOES``) em /fibras/acessos/. Antes a tela ficava aberta a qualquer
um logado, e o que cada um podia fazer saía da hierarquia (a "Ilha").

O recorte das vendas também é escolhido aqui: sem "Ver a loja" nem "Ver a rede",
a pessoa enxerga só as vendas em que ela é a vendedora.
"""
from functools import wraps

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect

# (chave, rótulo, o que libera, chaves que ela exige junto)
FUNCOES = [
    ('modulo.ver', 'Ver módulo', 'Kanban, lista, relatório e a ficha das vendas — só as que a pessoa vendeu.', []),
    ('loja.ver', 'Ver a loja', 'As vendas da loja dela (setor principal).', ['modulo.ver']),
    ('rede.ver', 'Ver a rede inteira', 'As vendas de todas as lojas.', ['modulo.ver']),
    ('status.alterar', 'Mudar status', 'Arrastar no Kanban, trocar na lista e na ficha, com o retorno da Ilha.',
     ['modulo.ver']),
    ('planilha.importar', 'Importar planilha', 'Subir a Esteira Vivo / Base analítica – FIXA no Kanban e na lista.',
     ['modulo.ver']),
    # A validação cruza a base da rede inteira: não existe "validação da minha loja".
    ('validacao.usar', 'Validação Base × Esteira', 'Cruzar, confirmar vínculos, importar a Esteira e sincronizar a base.',
     ['modulo.ver', 'rede.ver']),
    ('exportar', 'Exportar', 'Baixar Excel e o CSV de canceladas (só o que ela enxerga).', ['modulo.ver']),
    ('tratativa.abrir', 'Abrir tratativa e chat', 'Abrir incidente para a Ilha e conversar no chat reverso.',
     ['modulo.ver']),
]
CHAVES = {f[0] for f in FUNCOES}
EXIGE = {f[0]: f[3] for f in FUNCOES}
ROTULOS = {f[0]: f[1] for f in FUNCOES}


def is_superadmin(user) -> bool:
    return bool(
        user and getattr(user, 'is_authenticated', False)
        and (user.is_superuser or getattr(user, 'hierarchy', None) == 'SUPERADMIN')
    )


def completar(chaves):
    """Acrescenta o que cada função exige junto (mudar status sem ver o módulo não existe)."""
    saida = {c for c in chaves if c in CHAVES}
    for c in list(saida):
        saida.update(EXIGE.get(c, []))
    return sorted(saida)


def _acesso(user):
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if not hasattr(user, '_fibras_acesso'):
        from .models import AcessoFibras
        try:
            user._fibras_acesso = AcessoFibras.objects.filter(user=user, ativo=True).first()
        except Exception:  # noqa: BLE001 — tabela não migrada noutro servidor
            user._fibras_acesso = None
    return user._fibras_acesso


def chaves_de(user):
    if is_superadmin(user):
        return set(CHAVES)
    a = _acesso(user)
    return set(completar(a.permissoes or [])) if a else set()


def pode(user, chave):
    return chave in chaves_de(user)


def pode_no_template(user):
    """As chaves com "_" no lugar do ponto, para ``{% if fb_pode.status_alterar %}``."""
    saida = {c.replace('.', '_'): True for c in chaves_de(user)}
    saida['acessos'] = is_superadmin(user)
    return saida


def exige(*chaves):
    """Barra a view se a pessoa não tem nenhuma das chaves (JSON para quem chamou por fetch)."""
    def decorador(view):
        @wraps(view)
        def _view(request, *args, **kwargs):
            u = request.user
            if not any(pode(u, c) for c in chaves):
                aviso = 'Esta parte de Fibras não está liberada para o seu usuário.'
                if (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                        or 'application/json' in request.headers.get('Accept', '')
                        or request.content_type == 'application/json'):
                    return JsonResponse({'ok': False, 'error': aviso, 'erro': aviso}, status=403)
                messages.error(request, aviso)
                return redirect('fibras:kanban' if pode(u, 'modulo.ver') else 'home')
            return view(request, *args, **kwargs)
        return _view
    return decorador


def fibras_visiveis(user):
    """As vendas que a pessoa enxerga: rede, loja (setor principal) ou só as dela."""
    from .models import Fibra
    from .services import _normalize

    qs = Fibra.objects.all()
    chaves = chaves_de(user)
    if 'rede.ver' in chaves:
        return qs
    if 'modulo.ver' not in chaves:
        return qs.none()
    cond = Q(pk__in=[])
    loja = getattr(user, 'sector', None)
    if 'loja.ver' in chaves and loja and (loja.name or '').strip():
        cond |= Q(pdv__icontains=loja.name.strip())
    # A Base de Vendas guarda o vendedor por nome; compara sem acento e sem caixa.
    nome = _normalize(user.get_full_name() or user.username)
    proprias = [f.id for f in qs.exclude(vendedor='').only('id', 'vendedor') if _normalize(f.vendedor) == nome]
    return qs.filter(cond | Q(id__in=proprias))
