"""Mapa de localização das pessoas.

Módulo oculto: fora do menu e restrito à administração. A regra de acesso é
conferida no servidor, na tela e na API — endereço não divulgado não é controle
de acesso.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from users.models import Sector, User

from .models import ConfiguracaoMapa, PosicaoRegistrada
from .permissions import pode_ver_mapa
from .servicos import DIAS_PADRAO, FRESCO_MINUTOS, posicoes


def _payload(i):
    """Formato único de posição — a tela e a API leem a mesma coisa."""
    return {
        'id': i['id'], 'nome': i['nome'], 'cargo': i['cargo'], 'setor': i['setor'],
        'foto': i['foto'], 'lat': i['latitude'], 'lon': i['longitude'],
        'precisao': i['precisao'], 'quando': i['quando'], 'recente': i['recente'],
        'ao_vivo': i['ao_vivo'], 'origem': i['origem'],
        'momento': i['momento'].strftime('%d/%m/%Y %H:%M'),
    }


def _negar(request):
    messages.error(request, 'Acesso restrito.')
    return redirect('home')


def _filtrar(request):
    """Recorte de pessoas escolhido na tela."""
    pessoas = User.objects.filter(is_active=True).select_related('sector')

    setor_id = (request.GET.get('setor') or '').strip()
    usuario_id = (request.GET.get('usuario') or '').strip()
    busca = (request.GET.get('q') or '').strip()

    if setor_id.isdigit():
        pessoas = pessoas.filter(sector_id=int(setor_id))
    if usuario_id.isdigit():
        pessoas = pessoas.filter(id=int(usuario_id))
    if busca:
        from django.db.models import Q
        pessoas = pessoas.filter(
            Q(first_name__icontains=busca) | Q(last_name__icontains=busca)
            | Q(email__icontains=busca) | Q(job_title__icontains=busca))

    return pessoas, {'setor': setor_id, 'usuario': usuario_id, 'q': busca}


@login_required
def mapa(request):
    if not pode_ver_mapa(request.user):
        return _negar(request)

    pessoas, filtros = _filtrar(request)
    try:
        dias = max(1, min(90, int(request.GET.get('dias') or DIAS_PADRAO)))
    except ValueError:
        dias = DIAS_PADRAO

    encontradas = posicoes(usuarios=pessoas, dias=dias)
    recentes = [p for p in encontradas if p['recente']]

    return render(request, 'maps/mapa.html', {
        'posicoes': encontradas,
        # O mesmo conteúdo que a API devolve, para o mapa já nascer desenhado.
        'posicoes_json': [_payload(i) for i in encontradas],
        'total': len(encontradas),
        'recentes': len(recentes),
        'ao_vivo': sum(1 for p in encontradas if p['ao_vivo']),
        'sem_posicao': pessoas.count() - len(encontradas),
        'setores': Sector.objects.all().order_by('name'),
        'pessoas': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'filtros': filtros,
        'dias': dias,
        'fresco_minutos': FRESCO_MINUTOS,
        'coleta_ativa': ConfiguracaoMapa.carregar().coleta_ativa,
        'agora': timezone.localtime(),
    })


@login_required
def api_posicoes(request):
    """JSON que a tela recarrega sozinha, para o mapa acompanhar o movimento."""
    if not pode_ver_mapa(request.user):
        return JsonResponse({'erro': 'Acesso restrito.'}, status=403)

    pessoas, _filtros = _filtrar(request)
    try:
        dias = max(1, min(90, int(request.GET.get('dias') or DIAS_PADRAO)))
    except ValueError:
        dias = DIAS_PADRAO

    itens = posicoes(usuarios=pessoas, dias=dias)
    return JsonResponse({
        'atualizado_em': timezone.localtime().strftime('%H:%M:%S'),
        'total': len(itens),
        'recentes': sum(1 for i in itens if i['recente']),
        'ao_vivo': sum(1 for i in itens if i['ao_vivo']),
        'posicoes': [_payload(i) for i in itens],
    })


@login_required
@require_POST
def api_minha_posicao(request):
    """Recebe a posição que o navegador da própria pessoa enviou.

    Só grava com a coleta ligada: sem isso, um POST direto nesta URL viraria um
    jeito de alimentar o mapa com o interruptor desligado.

    Cada pessoa só envia a si mesma — não há como informar outro usuário aqui.
    """
    config = ConfiguracaoMapa.carregar()
    if not config.coleta_ativa:
        return JsonResponse({'ok': False, 'motivo': 'coleta desligada'}, status=409)

    try:
        lat = float(request.POST.get('lat'))
        lon = float(request.POST.get('lon'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'motivo': 'coordenada inválida'}, status=400)

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return JsonResponse({'ok': False, 'motivo': 'coordenada fora da faixa'}, status=400)

    try:
        precisao = float(request.POST.get('precisao'))
    except (TypeError, ValueError):
        precisao = None

    PosicaoRegistrada.objects.create(
        usuario=request.user, latitude=lat, longitude=lon,
        precisao_metros=precisao, momento=timezone.now(),
        origem=PosicaoRegistrada.Origem.APP)
    return JsonResponse({'ok': True})
