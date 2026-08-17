"""Permissões, descoberta de usuários e cálculo de faixas do módulo IMPULSO."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect

from .models import GRUPO_ADM, GRUPO_GESTOR, Faixa

User = get_user_model()


# ---------------------------------------------------------------------------
# Papéis / permissões
# ---------------------------------------------------------------------------
def _in_group(user, group_name):
    """Membro de um CommunicationGroup (gerenciado em /users/manage/groups/)."""
    return user.communication_groups.filter(name__iexact=group_name).exists()


def is_impulso_manager(user):
    """Gestor do Impulso: superuser ou membro de GESTORES (IMPULSO)."""
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser:
        return True
    return _in_group(user, GRUPO_GESTOR)


def is_impulso_member(user):
    """Pode acessar o módulo: superuser, ESCRITÓRIO (ADM) ou gestor do Impulso."""
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser:
        return True
    return _in_group(user, GRUPO_ADM) or _in_group(user, GRUPO_GESTOR)


def get_colaboradores():
    """Usuários ativos do ESCRITÓRIO (ADM) — alvos possíveis de metas/feedbacks."""
    return (User.objects.filter(is_active=True,
                                communication_groups__name__iexact=GRUPO_ADM)
            .distinct().order_by('first_name', 'last_name'))


def get_gestores():
    return (User.objects.filter(is_active=True,
                                communication_groups__name__iexact=GRUPO_GESTOR)
            .distinct().order_by('first_name', 'last_name'))


def get_gestores_do_setor(user):
    """Gestores do Impulso que estão no MESMO setor do usuário.

    É a regra de quem pode receber a solicitação de meta de um colaborador.
    Usuário sem setor, ou setor sem gestor cadastrado, devolve vazio — a tela
    trata esse caso explicando o que fazer, em vez de oferecer um gestor de
    outro setor.
    """
    setor_id = getattr(user, 'sector_id', None)
    if not setor_id:
        return User.objects.none()
    return get_gestores().filter(sector_id=setor_id).exclude(id=user.id)


def impulso_member_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not is_impulso_member(request.user):
            messages.error(request, 'Você não tem acesso ao módulo Impulso.')
            return redirect('home')
        return view_func(request, *args, **kwargs)
    return _wrapped


def impulso_manager_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not is_impulso_manager(request.user):
            messages.error(request, 'Ação disponível apenas para gestores do Impulso.')
            return redirect('impulso:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped


# ---------------------------------------------------------------------------
# ACOMPANHAMENTO — faixas / medalhas
# ---------------------------------------------------------------------------
# Regra: Impulso = 100% | Ouro > 90% | Prata > 70% | Bronze = 0% a 70%.
FAIXA_IMPULSO = Faixa.IMPULSO
FAIXA_OURO = Faixa.OURO
FAIXA_PRATA = Faixa.PRATA
FAIXA_BRONZE = Faixa.BRONZE

# Confianças (C$) creditadas por mês premiado, pagas ao encerrar o ciclo.
CONFIANCAS_POR_MES = 100
FAIXAS_PREMIADAS = (FAIXA_IMPULSO, FAIXA_OURO)

FAIXAS = [
    {'nome': FAIXA_IMPULSO, 'label': 'Impulso', 'regra': '100%', 'cor': '#2563EB',
     'bg': 'bg-blue-100', 'text': 'text-blue-800', 'ring': 'ring-blue-300',
     'grad': 'from-blue-500 to-indigo-600', 'icon': 'fas fa-bolt', 'premiada': True},
    {'nome': FAIXA_OURO, 'label': 'Ouro', 'regra': 'Acima de 90%', 'cor': '#D4AF37',
     'bg': 'bg-amber-100', 'text': 'text-amber-800', 'ring': 'ring-amber-300',
     'grad': 'from-amber-400 to-yellow-500', 'icon': 'fas fa-trophy', 'premiada': True},
    {'nome': FAIXA_PRATA, 'label': 'Prata', 'regra': 'Acima de 70%', 'cor': '#94A3B8',
     'bg': 'bg-slate-200', 'text': 'text-slate-700', 'ring': 'ring-slate-300',
     'grad': 'from-slate-300 to-slate-500', 'icon': 'fas fa-medal', 'premiada': False},
    {'nome': FAIXA_BRONZE, 'label': 'Bronze', 'regra': 'De 0% a 70%', 'cor': '#B45309',
     'bg': 'bg-orange-100', 'text': 'text-orange-800', 'ring': 'ring-orange-300',
     'grad': 'from-orange-400 to-amber-700', 'icon': 'fas fa-award', 'premiada': False},
]


def faixa_info(nome):
    for f in FAIXAS:
        if f['nome'] == nome:
            return f
    return FAIXAS[-1]  # Bronze


def faixa_por_score(score):
    """Score 0-100 -> faixa. Impulso exige os 100 pontos."""
    try:
        s = float(score or 0)
    except (TypeError, ValueError):
        s = 0.0
    if s >= 100:
        return FAIXA_IMPULSO
    if s > 90:
        return FAIXA_OURO
    if s > 70:
        return FAIXA_PRATA
    return FAIXA_BRONZE


def calcular_faixa(colaborador, inicio=None, fim=None):
    """Atalho: pontuação do colaborador no período (padrão = mês corrente)."""
    from .scoring import calcular_pontuacao
    return calcular_pontuacao(colaborador, inicio=inicio, fim=fim)
