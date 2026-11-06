"""Acesso ao módulo de Vendas.

Só o SUPERADMIN entra de saída; ele libera, pessoa por pessoa, cada parte do
módulo (``FUNCOES``) em /vendas/acessos/. Quem entra sem "Ver a rede inteira"
continua com o recorte de antes: vê o que vendeu ou lançou e os clientes da
loja dele.
"""
from functools import wraps

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect

# (chave, rótulo, o que libera, chaves que ela exige junto)
FUNCOES = [
    ('modulo.ver', 'Ver módulo', 'Entrar em Vendas: as vendas que a pessoa fez ou lançou e a tabela de produtos.', []),
    ('rede.ver', 'Ver a rede inteira', 'Vendas e clientes de todas as lojas, com o ranking por loja e vendedor.',
     ['modulo.ver']),
    ('vendas.lancar', 'Lançar venda', 'Nova venda pela loja dele, com cadastro do cliente e conferência do Renova.',
     ['modulo.ver']),
    ('vendas.lancar_outros', 'Lançar por outra loja/vendedor', 'Escolher o PDV e o vendedor ao lançar.',
     ['modulo.ver', 'vendas.lancar']),
    ('vendas.exportar', 'Exportar vendas', 'Baixar as vendas do filtro em Excel/CSV (só o que ela enxerga).',
     ['modulo.ver']),
    ('clientes.ver', 'Consultar clientes', 'Lista e ficha dos clientes (da loja, ou da rede com "Ver a rede inteira").',
     ['modulo.ver']),
    ('clientes.sincronizar', 'Atualizar base do Vivo GO', 'Puxar agora os clientes do Vivo GO (o automático roda a cada 5 min).',
     ['modulo.ver', 'clientes.ver']),
    ('produtos.criar', 'Criar produto', 'Cadastrar item na tabela de produtos.', ['modulo.ver']),
    ('produtos.importar', 'Importar tabela de preços', 'Substituir a tabela de produtos pela planilha.', ['modulo.ver']),
    ('parametros.gerenciar', 'Parametrização', 'Planos, seguros/SVA, desconto Vivo+ e serial obrigatório.',
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
    """Acrescenta o que cada função exige junto (lançar sem ver o módulo não existe)."""
    saida = {c for c in chaves if c in CHAVES}
    for c in list(saida):
        saida.update(EXIGE.get(c, []))
    return sorted(saida)


def _acesso(user):
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if not hasattr(user, '_vendas_acesso'):
        from .models import AcessoVendas
        try:
            user._vendas_acesso = AcessoVendas.objects.filter(user=user, ativo=True).first()
        except Exception:  # noqa: BLE001 — tabela não migrada noutro servidor
            user._vendas_acesso = None
    return user._vendas_acesso


def chaves_de(user):
    if is_superadmin(user):
        return set(CHAVES)
    a = _acesso(user)
    return set(completar(a.permissoes or [])) if a else set()


def pode(user, chave):
    return chave in chaves_de(user)


def pode_no_template(user):
    """As chaves com "_" no lugar do ponto, para ``{% if vd_pode.vendas_lancar %}``."""
    saida = {c.replace('.', '_'): True for c in chaves_de(user)}
    saida['acessos'] = is_superadmin(user)
    return saida


def can_access_vendas(user) -> bool:
    return pode(user, 'modulo.ver')


def pode_lancar_venda(user) -> bool:
    return pode(user, 'vendas.lancar')


def ve_rede(user) -> bool:
    return pode(user, 'rede.ver')


def exige(*chaves):
    """Barra a view se a pessoa não tem nenhuma das chaves (JSON para quem chamou por fetch)."""
    def decorador(view):
        @wraps(view)
        def _view(request, *args, **kwargs):
            u = request.user
            if not any(pode(u, c) for c in chaves):
                aviso = 'Esta parte de Vendas não está liberada para o seu usuário.'
                if (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                        or 'application/json' in request.headers.get('Accept', '')
                        or request.content_type == 'application/json'):
                    return JsonResponse({'ok': False, 'erro': aviso, 'erros': [aviso]}, status=403)
                messages.error(request, aviso)
                return redirect('vendas:dashboard' if can_access_vendas(u) else 'home')
            return view(request, *args, **kwargs)
        return _view
    return decorador


def vendas_do_usuario(user, qs):
    """Recorta o queryset para o que a pessoa pode ver.

    Sem "Ver a rede inteira", enxerga a venda em que ela é o vendedor **ou**
    foi quem lançou — quem digita a venda de outro precisa conferir o que digitou.
    """
    if ve_rede(user):
        return qs
    return qs.filter(Q(vendedor=user) | Q(created_by=user))
