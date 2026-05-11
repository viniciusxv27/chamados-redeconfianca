from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from users.models import Sector, User
from .models import CoordinatorStoreAccess
from .services import (
    FACTOR_RANGE_SPECS,
    DEFAULT_META_BY_ROLE,
    PILLAR_ORDER,
    SIMULATOR_INPUT_PILLARS,
    VIEW_CHOICES,
    VIEW_PROJECAO,
    VIEW_REALIZADO,
    VIEW_SIMULADOR,
    ROLE_CONSULTOR,
    ROLE_COORDENADOR,
    ROLE_GERENTE,
    ROLE_SUPERADMIN,
    get_all_coordinators,
    get_all_consultors,
    get_all_gerentes,
    get_coordinator_sectors,
    get_factor_set,
    get_hunter_levels_from_request,
    get_user_role,
    compute_consultor_simulation,
    compute_gerente_simulation,
    compute_coordenador_simulation,
    update_factor_sets_from_post,
)


def is_superadmin(user: User) -> bool:
    return user.is_superuser or getattr(user, 'hierarchy', None) == 'SUPERADMIN'


def get_sector_users(user: User) -> list[User]:
    if not user.sector:
        return []
    return list(
        User.objects.filter(is_active=True, sector=user.sector).order_by('first_name', 'last_name')
    )


def get_sector_consultors(user: User) -> list[User]:
    if not user.sector:
        return []
    return list(
        User.objects.filter(is_active=True, sector=user.sector, hierarchy='PADRAO').order_by('first_name', 'last_name')
    )


@login_required
def simulator_dashboard(request):
    current_user = request.user
    role = get_user_role(current_user)
    hunter_levels = get_hunter_levels_from_request(request)

    # Modo de visualização (Realizado / Projeção / Simulador de fato)
    view_mode = request.GET.get('view') or VIEW_PROJECAO
    if view_mode not in {VIEW_PROJECAO, VIEW_REALIZADO, VIEW_SIMULADOR}:
        view_mode = VIEW_PROJECAO

    # Inputs do simulador (campos sim__<pilar>__<campo>)
    simulator_inputs = {}
    if view_mode == VIEW_SIMULADOR:
        for key in request.GET:
            if key.startswith('sim__'):
                simulator_inputs[key[len('sim__'):]] = request.GET.get(key)

    available_targets = []
    target_user = None
    target_role = None
    show_summary_only = False

    if role == ROLE_SUPERADMIN:
        coordinators = get_all_coordinators()
        gerentes = get_all_gerentes()
        consultors = get_all_consultors()

        available_targets = (
            [{'id': user.id, 'label': f"{user.get_full_name()} (Coordenador)", 'role': ROLE_COORDENADOR} for user in coordinators]
            + [{'id': user.id, 'label': f"{user.get_full_name()} (Gerente)", 'role': ROLE_GERENTE} for user in gerentes]
            + [{'id': user.id, 'label': f"{user.get_full_name()} (Consultor)", 'role': ROLE_CONSULTOR} for user in consultors]
        )

        target_user_id = request.GET.get('user_id')
        if target_user_id:
            target_user = get_object_or_404(User, id=target_user_id, is_active=True)
            target_role = get_user_role(target_user)
        else:
            show_summary_only = True
    elif role == ROLE_COORDENADOR:
        sectors = get_coordinator_sectors(current_user)
        users_qs = User.objects.filter(is_active=True, sector__in=sectors).order_by('first_name', 'last_name')
        available_targets = [
            {'id': user.id, 'label': user.get_full_name() or user.email, 'role': get_user_role(user)}
            for user in users_qs
        ]

        target_user_id = request.GET.get('user_id')
        if target_user_id:
            target_user = get_object_or_404(User, id=target_user_id, is_active=True)
            if target_user.sector not in sectors:
                messages.error(request, 'Este usuário não pertence às suas lojas.')
                return redirect('simulator:dashboard')
            target_role = get_user_role(target_user)
        else:
            target_user = current_user
            target_role = ROLE_COORDENADOR
            show_summary_only = True
    elif role == ROLE_GERENTE:
        sector_users = get_sector_users(current_user)
        available_targets = [
            {'id': user.id, 'label': user.get_full_name() or user.email, 'role': get_user_role(user)}
            for user in sector_users
        ]

        target_user_id = request.GET.get('user_id')
        if target_user_id:
            target_user = get_object_or_404(User, id=target_user_id, is_active=True)
            if target_user.sector != current_user.sector:
                messages.error(request, 'Este usuário não pertence ao seu setor.')
                return redirect('simulator:dashboard')
            target_role = get_user_role(target_user)
        else:
            target_user = current_user
            target_role = ROLE_GERENTE
    else:
        sector_users = get_sector_consultors(current_user)
        available_targets = [
            {'id': user.id, 'label': user.get_full_name() or user.email, 'role': get_user_role(user)}
            for user in sector_users
        ]
        target_user_id = request.GET.get('user_id')
        if target_user_id:
            target_user = get_object_or_404(User, id=target_user_id, is_active=True)
            if target_user.sector != current_user.sector:
                messages.error(request, 'Este usuário não pertence ao seu setor.')
                return redirect('simulator:dashboard')
            target_role = ROLE_CONSULTOR
        else:
            target_user = current_user
            target_role = ROLE_CONSULTOR

    simulation = None
    if target_user and target_role:
        factor_set = get_factor_set(target_role)
        if target_role == ROLE_CONSULTOR:
            simulation = compute_consultor_simulation(target_user, factor_set.data, hunter_levels, view_mode=view_mode, simulator_inputs=simulator_inputs)
        elif target_role == ROLE_GERENTE:
            simulation = compute_gerente_simulation(target_user, factor_set.data, hunter_levels, view_mode=view_mode, simulator_inputs=simulator_inputs)
        elif target_role == ROLE_COORDENADOR:
            simulation = compute_coordenador_simulation(target_user, factor_set.data, hunter_levels, view_mode=view_mode, simulator_inputs=simulator_inputs)

    context = {
        'user': current_user,
        'role': role,
        'available_targets': available_targets,
        'target_user': target_user,
        'target_role': target_role,
        'simulation': simulation,
        'show_summary_only': show_summary_only,
        'is_superadmin': is_superadmin(current_user),
        'hunter_levels': hunter_levels,
        'pillars': PILLAR_ORDER,
        'view_mode': view_mode,
        'view_choices': VIEW_CHOICES,
        'simulator_input_pillars': SIMULATOR_INPUT_PILLARS,
        'simulator_inputs': simulator_inputs,
    }

    return render(request, 'simulator/dashboard.html', context)


@login_required
def simulator_admin_factors(request):
    if not is_superadmin(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('simulator:dashboard')

    if request.method == 'POST':
        update_factor_sets_from_post(request.POST, request.user)
        messages.success(request, 'Fatores atualizados com sucesso.')
        return redirect('simulator:admin_factors')

    factor_sets = {
        ROLE_CONSULTOR: get_factor_set(ROLE_CONSULTOR),
        ROLE_GERENTE: get_factor_set(ROLE_GERENTE),
        ROLE_COORDENADOR: get_factor_set(ROLE_COORDENADOR),
    }

    context = {
        'factor_sets': factor_sets,
        'range_specs': FACTOR_RANGE_SPECS,
        'meta_specs': DEFAULT_META_BY_ROLE,
    }
    return render(request, 'simulator/admin_factors.html', context)


@login_required
def simulator_admin_stores(request):
    if not is_superadmin(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('simulator:dashboard')

    coordinators = get_all_coordinators()
    selected_coordinator = None
    sectors = []
    selected_sectors = []

    coordinator_id = request.GET.get('coordinator_id')
    if coordinator_id:
        selected_coordinator = get_object_or_404(User, id=coordinator_id, is_active=True)
        sectors = list(Sector.objects.all().order_by('name'))
        access = CoordinatorStoreAccess.objects.filter(coordinator=selected_coordinator).first()
        if access:
            selected_sectors = list(access.sectors.values_list('id', flat=True))

    if request.method == 'POST':
        coordinator_id = request.POST.get('coordinator_id')
        selected_coordinator = get_object_or_404(User, id=coordinator_id, is_active=True)
        sector_ids = request.POST.getlist('sectors')
        access, _ = CoordinatorStoreAccess.objects.get_or_create(coordinator=selected_coordinator)
        access.sectors.set(Sector.objects.filter(id__in=sector_ids))
        access.updated_by = request.user
        access.save()
        messages.success(request, 'Lojas atualizadas com sucesso.')
        return redirect(f"simulator:admin_stores?coordinator_id={selected_coordinator.id}")

    context = {
        'coordinators': coordinators,
        'selected_coordinator': selected_coordinator,
        'sectors': sectors,
        'selected_sectors': selected_sectors,
    }
    return render(request, 'simulator/admin_stores.html', context)
