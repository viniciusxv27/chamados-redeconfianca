from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import PortalPopupForm
from .models import PortalPopup


def _can_manage_popups(user):
    return user.is_authenticated and (
        user.is_superuser or getattr(user, 'hierarchy', None) == 'SUPERADMIN'
    )


# --------------------------------------------------------------------------- #
# Conclusão pelo usuário final (modos "Ciente" e "Visitar link")
# --------------------------------------------------------------------------- #
@login_required
@require_POST
def complete_popup(request, pk):
    popup = get_object_or_404(PortalPopup, pk=pk)
    if not popup.applies_to(request.user):
        return JsonResponse({'success': False, 'error': 'Popup não se aplica a você.'}, status=403)
    popup.mark_completed(request.user)
    return JsonResponse({'success': True})


# --------------------------------------------------------------------------- #
# Gestão (superadmin)
# --------------------------------------------------------------------------- #
@login_required
def popup_list(request):
    if not _can_manage_popups(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('home')

    popups = PortalPopup.objects.all().order_by('order', 'id')
    return render(request, 'portal_popups/popup_list.html', {'popups': popups})


@login_required
def popup_create(request):
    if not _can_manage_popups(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('home')

    if request.method == 'POST':
        form = PortalPopupForm(request.POST)
        if form.is_valid():
            popup = form.save(commit=False)
            popup.created_by = request.user
            popup.save()
            form.save_m2m()
            messages.success(request, 'Popup criado com sucesso.')
            return redirect('portal_popups:list')
    else:
        form = PortalPopupForm()

    return render(request, 'portal_popups/popup_form.html', {'form': form, 'is_edit': False})


@login_required
def popup_edit(request, pk):
    if not _can_manage_popups(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('home')

    popup = get_object_or_404(PortalPopup, pk=pk)
    if request.method == 'POST':
        form = PortalPopupForm(request.POST, instance=popup)
        if form.is_valid():
            form.save()
            messages.success(request, 'Popup atualizado com sucesso.')
            return redirect('portal_popups:list')
    else:
        form = PortalPopupForm(instance=popup)

    return render(request, 'portal_popups/popup_form.html',
                  {'form': form, 'is_edit': True, 'popup': popup})


@login_required
@require_POST
def popup_toggle(request, pk):
    if not _can_manage_popups(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('home')

    popup = get_object_or_404(PortalPopup, pk=pk)
    popup.is_active = not popup.is_active
    popup.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, f'Popup "{popup.title}" {"ativado" if popup.is_active else "desativado"}.')
    return redirect('portal_popups:list')


@login_required
@require_POST
def popup_delete(request, pk):
    if not _can_manage_popups(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('home')

    popup = get_object_or_404(PortalPopup, pk=pk)
    title = popup.title
    popup.delete()
    messages.success(request, f'Popup "{title}" excluído.')
    return redirect('portal_popups:list')
