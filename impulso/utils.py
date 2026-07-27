"""Permissões, descoberta de usuários e cálculo de faixas do módulo IMPULSO."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect

from .models import GRUPO_ADM, GRUPO_GESTOR, Meta

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
# ACOMPANHAMENTO — faixas
# ---------------------------------------------------------------------------
# Faixas por pontuação (0-100). A pontuação vem da média das notas do gestor
# (qualidade + prazo, 0-5 cada => 0-10 por meta => escala 0-100).
FAIXA_OURO = 'OURO'
FAIXA_PRATA = 'PRATA'
FAIXA_BRONZE = 'BRONZE'
FAIXA_IMPULSO = 'IMPULSO'

FAIXAS = [
    {'nome': FAIXA_OURO, 'label': 'Ouro', 'min': 90, 'cor': '#D4AF37',
     'bg': 'bg-yellow-100', 'text': 'text-yellow-800', 'icon': 'fas fa-trophy'},
    {'nome': FAIXA_PRATA, 'label': 'Prata', 'min': 75, 'cor': '#9CA3AF',
     'bg': 'bg-gray-200', 'text': 'text-gray-700', 'icon': 'fas fa-medal'},
    {'nome': FAIXA_BRONZE, 'label': 'Bronze', 'min': 60, 'cor': '#B45309',
     'bg': 'bg-orange-100', 'text': 'text-orange-800', 'icon': 'fas fa-award'},
    {'nome': FAIXA_IMPULSO, 'label': 'Impulso', 'min': 0, 'cor': '#2563EB',
     'bg': 'bg-blue-100', 'text': 'text-blue-800', 'icon': 'fas fa-bolt'},
]


def faixa_info(nome):
    for f in FAIXAS:
        if f['nome'] == nome:
            return f
    return FAIXAS[-1]


def faixa_por_score(score):
    for f in FAIXAS:  # ordenadas do maior mínimo para o menor
        if score >= f['min']:
            return f['nome']
    return FAIXA_IMPULSO


def calcular_faixa(colaborador, inicio=None, fim=None):
    """Calcula a faixa do colaborador a partir das metas avaliadas.

    Retorna dict com: faixa, score (0-100), total, concluidas, avaliadas,
    media_qualidade, media_prazo.
    """
    qs = Meta.objects.filter(colaborador=colaborador)
    if inicio:
        qs = qs.filter(created_at__date__gte=inicio)
    if fim:
        qs = qs.filter(created_at__date__lte=fim)

    total = qs.count()
    concluidas = qs.filter(status=Meta.Status.CONCLUIDA).count()
    avaliadas = qs.filter(nota_qualidade__isnull=False, nota_prazo__isnull=False)
    n_aval = avaliadas.count()

    if n_aval == 0:
        return {
            'faixa': FAIXA_IMPULSO, 'score': 0, 'total': total,
            'concluidas': concluidas, 'avaliadas': 0,
            'media_qualidade': None, 'media_prazo': None,
        }

    soma_q = soma_p = 0
    for m in avaliadas:
        soma_q += m.nota_qualidade
        soma_p += m.nota_prazo
    media_q = soma_q / n_aval
    media_p = soma_p / n_aval
    # (0-5)+(0-5)=0-10 por meta; média/10*100 => 0-100
    score = round(((media_q + media_p) / 10.0) * 100, 1)

    return {
        'faixa': faixa_por_score(score), 'score': score, 'total': total,
        'concluidas': concluidas, 'avaliadas': n_aval,
        'media_qualidade': round(media_q, 1), 'media_prazo': round(media_p, 1),
    }
