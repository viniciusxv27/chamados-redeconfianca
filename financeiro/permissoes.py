"""Quem faz o quê no Financeiro.

O SUPERADMIN tem tudo. Mais ninguém entra, a não ser quem ele liberar em
/financeiro/acessos/ — parte por parte (área) e ação por ação, e, se quiser,
só para algumas lojas.

Cada permissão é uma chave "área.ação". Qualquer ação de uma área já deixa ver
a área (quem baixa título precisa enxergar o título).
"""
from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

# área: (rótulo, ícone, descrição, [(ação, rótulo da ação)])
AREAS = [
    ('caixa', 'Caixa das lojas', 'fa-store',
     'Visão por loja: conciliação de cartões, contas a pagar e a receber, pagas e recebidas.',
     [('ver', 'Ver')]),
    ('pagar', 'Contas a pagar', 'fa-file-invoice-dollar',
     'Títulos a pagar: lançar, editar, parcelar e cancelar.',
     [('ver', 'Ver'), ('editar', 'Lançar e editar'), ('cancelar', 'Cancelar e excluir')]),
    ('receber', 'Contas a receber', 'fa-hand-holding-dollar',
     'Títulos a receber: lançar, editar, parcelar e cancelar.',
     [('ver', 'Ver'), ('editar', 'Lançar e editar'), ('cancelar', 'Cancelar e excluir')]),
    ('baixas', 'Baixa de títulos', 'fa-circle-check',
     'Dar baixa (pagar/receber), inclusive em lote; editar e estornar as contas pagas e recebidas.',
     [('baixar', 'Dar baixa'), ('estornar', 'Editar e estornar baixas')]),
    ('bancos', 'Contas bancárias', 'fa-building-columns',
     'Cadastro das contas, extrato e lançamentos avulsos (tarifa, transferência).',
     [('ver', 'Ver'), ('editar', 'Cadastrar e editar contas'), ('lancar', 'Lançar avulso e transferir')]),
    ('conciliacao', 'Conciliação bancária', 'fa-scale-balanced',
     'Importar o extrato do banco (OFX/CSV) e casar com os movimentos.',
     [('ver', 'Ver'), ('conciliar', 'Importar e conciliar')]),
    ('cartoes', 'Conciliação de cartões', 'fa-credit-card',
     'Importar o relatório da adquirente e conferir contra as vendas no cartão.',
     [('ver', 'Ver'), ('conciliar', 'Importar e conciliar')]),
    ('plano', 'Plano de contas', 'fa-sitemap',
     'As contas de receita e despesa que classificam os títulos.',
     [('ver', 'Ver'), ('editar', 'Criar e editar')]),
    ('auditoria', 'Rastreabilidade', 'fa-fingerprint',
     'Quem fez cada movimento, quando, de onde, com o antes e o depois.',
     [('ver', 'Ver')]),
]
ROTULO_AREA = {a[0]: a[1] for a in AREAS}
CHAVES = {f'{a[0]}.{acao}' for a in AREAS for acao, _r in a[4]}


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def _acesso(user):
    """O AcessoFinanceiro ativo da pessoa (cacheado no request), ou None."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if not hasattr(user, '_fin_acesso'):
        from .models import AcessoFinanceiro
        try:
            acesso = (AcessoFinanceiro.objects.prefetch_related('lojas')
                      .filter(user=user, ativo=True).first())
            if acesso is not None:
                acesso._lojas_ids = {l.id for l in acesso.lojas.all()}
        except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
            acesso = None
        user._fin_acesso = acesso
    return user._fin_acesso


def chaves_de(user):
    if e_superadmin(user):
        return set(CHAVES)
    acesso = _acesso(user)
    if not acesso:
        return set()
    chaves = {c for c in (acesso.permissoes or []) if c in CHAVES}
    # Qualquer ação da área deixa ver a área.
    chaves |= {f'{c.split(".")[0]}.ver' for c in chaves if f'{c.split(".")[0]}.ver' in CHAVES}
    return chaves


def pode(user, chave):
    """`chave` é "área.ação" (ex.: "pagar.editar") ou só "área" (= ver)."""
    if '.' not in chave:
        return any(c.startswith(chave + '.') for c in chaves_de(user))
    return chave in chaves_de(user)


def pode_entrar(user):
    if e_superadmin(user) or bool(chaves_de(user)):
        return True
    from .fechamento import pode_ver                       # quem só tem o Fechamento de caixa
    return pode_ver(user)


def lojas_permitidas(user):
    """None = todas as lojas; senão o conjunto de ids das lojas liberadas."""
    if e_superadmin(user):
        return None
    acesso = _acesso(user)
    if not acesso or not getattr(acesso, '_lojas_ids', None):
        return None
    return acesso._lojas_ids


def filtrar_por_loja(qs, user, campo='loja'):
    ids = lojas_permitidas(user)
    if ids is None:
        return qs
    return qs.filter(**{f'{campo}__in': ids})


def ve_loja(user, loja_id):
    ids = lojas_permitidas(user)
    return ids is None or (loja_id in ids)


def _quer_json(request):
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in request.headers.get('Accept', ''))


def exige(*chaves):
    """Trava de view: precisa de ao menos uma das chaves (ou entrar, sem chave)."""
    def decorador(view):
        @wraps(view)
        def _view(request, *args, **kwargs):
            user = request.user
            ok = pode_entrar(user) if not chaves else any(pode(user, c) for c in chaves)
            if not ok:
                aviso = 'Esta parte do Financeiro não está liberada para o seu usuário.'
                if _quer_json(request):
                    return JsonResponse({'ok': False, 'erro': aviso}, status=403)
                messages.error(request, aviso)
                return redirect('financeiro:painel' if pode_entrar(user) else 'home')
            return view(request, *args, **kwargs)
        return _view
    return decorador
