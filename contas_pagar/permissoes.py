"""Quem entra no Contas a Pagar e com qual papel.

SUPERADMIN sempre (e só ele edita a lista em /contas-a-pagar/acessos/). Fora
dele, só quem está em ``AcessoContasPagar``, pessoa por pessoa:

- CONSULTA: Painel e Histórico.
- OPERADOR: + Pagar hoje (importar, montar, publicar) e excluir dia do histórico.
- CONFIGURADOR: + Configurações (contas bancárias, categorias, regra do Agging).
"""
from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

SUPERADMIN = 'SUPERADMIN'
CONFIGURADOR = 'CONFIGURADOR'
OPERADOR = 'OPERADOR'
CONSULTA = 'CONSULTA'

NIVEL = {CONSULTA: 1, OPERADOR: 2, CONFIGURADOR: 3, SUPERADMIN: 4}


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def papel(user):
    """'SUPERADMIN', 'CONFIGURADOR', 'OPERADOR', 'CONSULTA' ou None (uma consulta por request)."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if e_superadmin(user):
        return SUPERADMIN
    if not hasattr(user, '_contas_pagar_papel'):
        from .models import AcessoContasPagar
        try:
            user._contas_pagar_papel = (AcessoContasPagar.objects.filter(user=user)
                                        .values_list('papel', flat=True).first())
        except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
            user._contas_pagar_papel = None
    return user._contas_pagar_papel


def nivel(user):
    return NIVEL.get(papel(user), 0)


def pode_consultar(user):
    return nivel(user) >= NIVEL[CONSULTA]


def pode_ver(user):
    """Para o menu: SUPERADMIN ou quem o SUPERADMIN liberou (qualquer papel)."""
    return pode_consultar(user)


def pode_gerir_acessos(user):
    """Só o SUPERADMIN mexe na lista de acessos (nem o CONFIGURADOR)."""
    return e_superadmin(user)


def pode_operar(user):
    return nivel(user) >= NIVEL[OPERADOR]


def pode_configurar(user):
    return nivel(user) >= NIVEL[CONFIGURADOR]


def contexto(user):
    """O que os templates usam para mostrar/esconder abas e botões."""
    return {'cp_papel': papel(user), 'cp_superadmin': e_superadmin(user), 'cp_operar': pode_operar(user),
            'cp_configurar': pode_configurar(user)}


MENSAGEM = {
    CONSULTA: 'O Contas a Pagar não está liberado para você.',
    OPERADOR: 'Só quem opera o Contas a Pagar entra no Pagar hoje.',
    CONFIGURADOR: 'Só quem configura o Contas a Pagar mexe nas configurações.',
    SUPERADMIN: 'Só o SUPERADMIN libera o Contas a Pagar.',
}


def exige(minimo=CONSULTA, api=False):
    """Barra quem não tem o papel mínimo: tela volta para a home; API responde 403 em JSON."""
    def decorador(view):
        @wraps(view)
        def _view(request, *args, **kwargs):
            user = request.user
            if not getattr(user, 'is_authenticated', False):
                if api:
                    return JsonResponse({'erro': 'Entre no portal de novo.'}, status=401)
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path())
            if nivel(user) < NIVEL[minimo]:
                if api:
                    return JsonResponse({'erro': MENSAGEM[minimo]}, status=403)
                messages.error(request, MENSAGEM[minimo])
                return redirect('contas_pagar:painel' if pode_consultar(user) else 'home')
            return view(request, *args, **kwargs)
        return _view
    return decorador
