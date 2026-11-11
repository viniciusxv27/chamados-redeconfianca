"""Quem entra na Auditoria D-1 e com qual papel.

Por padrão SÓ o SUPERADMIN entra (critério do portal: is_superuser ou hierarchy == 'SUPERADMIN'). Fora dele,
só quem o SUPERADMIN liberou em /auditoria-d1/acessos/, pessoa por pessoa:

- CONSULTA: vê a esteira e baixa a planilha do mês;
- AUDITOR:  + grava verificação, correção, contestação e exporta o CSV de importação do VivoGo;
- GESTOR:   + importa as bases SVA/Fixa, aplica os cruzamentos e dispara o WhatsApp das lojas.

A lista de acessos e a configuração (WhatsApp) são só do SUPERADMIN — nem o GESTOR vê.
"""
from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

SUPERADMIN = 'SUPERADMIN'
CONSULTA = 'CONSULTA'
AUDITOR = 'AUDITOR'
GESTOR = 'GESTOR'
NIVEL = {CONSULTA: 1, AUDITOR: 2, GESTOR: 3, SUPERADMIN: 4}


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def papel(user):
    """'SUPERADMIN', 'GESTOR', 'AUDITOR', 'CONSULTA' ou None (uma consulta por request)."""
    if not (user and getattr(user, 'is_authenticated', False) and getattr(user, 'is_active', True)):
        return None
    if e_superadmin(user):
        return SUPERADMIN
    if not hasattr(user, '_auditoria_d1_papel'):
        from .models import AcessoAuditoriaD1
        try:
            user._auditoria_d1_papel = (AcessoAuditoriaD1.objects.filter(user=user)
                                        .values_list('papel', flat=True).first())
        except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
            user._auditoria_d1_papel = None
    return user._auditoria_d1_papel if user._auditoria_d1_papel in NIVEL else None


def pode(user, minimo):
    p = papel(user)
    return bool(p) and NIVEL[p] >= NIVEL[minimo]


def pode_ver(user):
    """Para o menu: a pessoa entra no módulo (SUPERADMIN ou liberada na lista)."""
    return pode(user, CONSULTA)


def capacidades(user):
    """O que a tela libera (vai para o JS e para o template)."""
    return {'papel': papel(user), 'auditar': pode(user, AUDITOR), 'gerir': pode(user, GESTOR),
            'superadmin': pode(user, SUPERADMIN)}


def exige(minimo, api=False):
    """Barra quem não tem o papel mínimo: página volta para a home com aviso; API responde 401/403 em JSON."""
    def decorador(view):
        @wraps(view)
        def _view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if api:
                    return JsonResponse({'erro': 'Faça login no portal.'}, status=401)
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path())
            if not pode(request.user, minimo):
                if api:
                    return JsonResponse({'erro': 'Sem permissão para esta ação na Auditoria D-1.'}, status=403)
                messages.error(request, 'Só o SUPERADMIN mexe nisso.' if minimo == SUPERADMIN
                               else 'A Auditoria D-1 não está liberada para você.')
                return redirect('home')
            return view(request, *args, **kwargs)
        return _view
    return decorador
