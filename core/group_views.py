from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST
import json

User = get_user_model()


def user_is_superadmin(user):
    """Check if user is superuser or in superadmin group"""
    return user.is_superuser or user.groups.filter(name='Superadmin').exists()


@login_required
def group_management(request):
    """Main group management page"""
    if not user_is_superadmin(request.user):
        messages.error(request, 'Acesso negado. Apenas superadministradores podem gerenciar grupos.')
        return redirect('dashboard')
    
    # Get or create management groups
    management_groups = []
    group_names = [
        'Gestores de Fornecedores',
        'Gestores de Compras', 
        'Gestores de Projetos'
    ]
    
    for group_name in group_names:
        group, created = Group.objects.get_or_create(name=group_name)
        management_groups.append({
            'group': group,
            'user_count': group.user_set.count(),
            'created': created
        })
    
    # Get users for assignment
    search_query = request.GET.get('search', '')
    User = get_user_model()
    users_qs = User.objects.filter(is_active=True).order_by('first_name', 'last_name', 'username')
    
    if search_query:
        users_qs = users_qs.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(username__icontains=search_query) |
            Q(email__icontains=search_query)
        )
    
    paginator = Paginator(users_qs, 10)
    page_number = request.GET.get('page')
    users = paginator.get_page(page_number)
    
    # Add group information to users
    for user in users:
        user.management_groups = user.groups.filter(name__in=group_names)
    
    context = {
        'management_groups': management_groups,
        'users': users,
        'search_query': search_query,
        'group_names': group_names,
        'total_users': User.objects.filter(is_active=True).count(),
    }
    
    return render(request, 'core/group_management.html', context)


@login_required
def group_detail(request, group_id):
    """Detail view for a specific group"""
    if not user_is_superadmin(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')
    
    group = get_object_or_404(Group, id=group_id)
    
    # Get users in this group
    users_in_group = group.user_set.filter(is_active=True).order_by('first_name', 'last_name', 'username')
    
    # Get users not in this group
    search_query = request.GET.get('search', '')
    User = get_user_model()
    available_users_qs = User.objects.filter(is_active=True).exclude(groups=group).order_by('first_name', 'last_name', 'username')
    
    if search_query:
        available_users_qs = available_users_qs.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(username__icontains=search_query) |
            Q(email__icontains=search_query)
        )
    
    paginator = Paginator(available_users_qs, 10)
    page_number = request.GET.get('page')
    available_users = paginator.get_page(page_number)
    
    context = {
        'group': group,
        'users_in_group': users_in_group,
        'available_users': available_users,
        'search_query': search_query,
    }
    
    return render(request, 'core/group_detail.html', context)


@login_required
@require_POST
def add_user_to_group(request):
    """AJAX endpoint to add user to group"""
    if not user_is_superadmin(request.user):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
        group_id = data.get('group_id')
        
        user = get_object_or_404(User, id=user_id)
        group = get_object_or_404(Group, id=group_id)
        
        # Add user to group
        user.groups.add(group)
        
        return JsonResponse({
            'success': True, 
            'message': f'{user.get_full_name() or user.username} foi adicionado ao grupo {group.name}'
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def remove_user_from_group(request):
    """AJAX endpoint to remove user from group"""
    if not user_is_superadmin(request.user):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
        group_id = data.get('group_id')
        
        user = get_object_or_404(User, id=user_id)
        group = get_object_or_404(Group, id=group_id)
        
        # Remove user from group
        user.groups.remove(group)
        
        return JsonResponse({
            'success': True, 
            'message': f'{user.get_full_name() or user.username} foi removido do grupo {group.name}'
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST 
def bulk_assign_groups(request):
    """AJAX endpoint for bulk group assignment"""
    if not user_is_superadmin(request.user):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    try:
        data = json.loads(request.body)
        user_ids = data.get('user_ids', [])
        group_ids = data.get('group_ids', [])
        action = data.get('action')  # 'add' or 'remove'
        
        User = get_user_model()
        users = User.objects.filter(id__in=user_ids, is_active=True)
        groups = Group.objects.filter(id__in=group_ids)
        
        count = 0
        for user in users:
            for group in groups:
                if action == 'add':
                    user.groups.add(group)
                    count += 1
                elif action == 'remove':
                    user.groups.remove(group)
                    count += 1
        
        action_text = 'adicionados aos' if action == 'add' else 'removidos dos'
        return JsonResponse({
            'success': True,
            'message': f'{len(user_ids)} usuário(s) foram {action_text} grupos selecionados'
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})