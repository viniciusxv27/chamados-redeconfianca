"""Trilhas e Treinamentos: um módulo só para quem usa, dois apps por baixo.

As tabelas, as URLs e as regras de cada parte continuam onde sempre estiveram.
O que é comum mora aqui: quem vê cada aba. A aba só repete a regra que a própria
tela aplica — a porta continua sendo a view, e esconder a aba não fecha nada.
"""
from django.urls import reverse


def pode_ver_trilhas(user):
    """As telas de trilha pedem login, e só."""
    return bool(user and getattr(user, 'is_authenticated', False))


def pode_ver_treinamentos(user):
    """A lista de treinamentos não pede nada (trainings_list_view é aberta)."""
    return True


def pode_gerir_trilhas(user):
    """Quem cria trilha: a mesma regra de create_trail.

    O SUPERVISOR entra, mas só enxerga as trilhas dos setores dele; a regra por
    trilha continua em views._can_manage_trail.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    return bool(user.is_superuser
                or getattr(user, 'hierarchy', '') in ('SUPERADMIN', 'ADMIN', 'SUPERVISOR'))


def pode_gerir_treinamentos(user):
    """A chave de sempre do catálogo de acessos (regra normal ou liberação individual)."""
    from users.module_access import tem_acesso
    return tem_acesso(user, 'treinamentos.gestao')


def abas_de(user):
    """As abas que esta pessoa vê, na ordem da tela."""
    abas = []
    if pode_ver_trilhas(user):
        abas.append({'chave': 'trilhas', 'rotulo': 'Trilhas', 'icone': 'fas fa-route',
                     'url': reverse('knowledge_trails:dashboard')})
    if pode_ver_treinamentos(user):
        abas.append({'chave': 'treinamentos', 'rotulo': 'Treinamentos', 'icone': 'fas fa-play-circle',
                     'url': reverse('trainings_list')})
    if pode_gerir_trilhas(user):
        abas.append({'chave': 'gerir_trilhas', 'rotulo': 'Gerenciar trilhas', 'icone': 'fas fa-sliders',
                     'url': reverse('knowledge_trails:manage_trails')})
    if pode_gerir_treinamentos(user):
        abas.append({'chave': 'gerir_treinamentos', 'rotulo': 'Gerenciar treinamentos', 'icone': 'fas fa-cog',
                     'url': reverse('trainings_manage')})
    return abas
