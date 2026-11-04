"""Mapa de localização das pessoas.

Módulo oculto: fora do menu e restrito à administração. A regra de acesso é
conferida no servidor, na tela e na API — endereço não divulgado não é controle
de acesso.
"""
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils import timezone

from users.models import Sector, User

from . import tiles
from .models import ConfiguracaoMapa, PosicaoRegistrada
from .permissions import pode_ver_mapa
from .servicos import (
    DIAS_PADRAO, FRESCO_MINUTOS, JANELA_MOMENTO_MINUTOS, posicao_no_momento, posicoes,
)


def _payload(i):
    """Formato único de posição — a tela e a API leem a mesma coisa."""
    return {
        'id': i['id'], 'nome': i['nome'], 'cargo': i['cargo'], 'setor': i['setor'],
        'foto': i['foto'], 'lat': i['latitude'], 'lon': i['longitude'],
        'precisao': i['precisao'], 'quando': i['quando'], 'recente': i['recente'],
        'ao_vivo': i['ao_vivo'], 'origem': i['origem'],
        # Hora de Brasília: o banco devolve em UTC, e o balão mostrava 3 horas a mais.
        'momento': timezone.localtime(i['momento']).strftime('%d/%m/%Y %H:%M'),
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


def _momento_pedido(request):
    """`?usuario=<id>&em=<data e hora>`: uma pessoa num momento, vindo do histórico.

    A URL leva só quem e quando; a posição é buscada aqui, depois da permissão. Inclui
    ex-colaborador, que o recorte normal do mapa deixa de fora, porque o histórico
    continua falando dele. Devolve (pessoa, momento) ou (None, None).
    """
    usuario_id = (request.GET.get('usuario') or '').strip()
    em = (request.GET.get('em') or '').strip()
    if not (usuario_id.isdigit() and em):
        return None, None
    try:
        momento = timezone.make_aware(datetime.strptime(em, '%Y-%m-%dT%H:%M:%S'))
    except ValueError:
        return None, None
    pessoa = User.objects.select_related('sector').filter(id=int(usuario_id)).first()
    return (pessoa, momento) if pessoa else (None, None)


@login_required
def mapa(request):
    if not pode_ver_mapa(request.user):
        return _negar(request)

    pessoas, filtros = _filtrar(request)
    try:
        dias = max(1, min(90, int(request.GET.get('dias') or DIAS_PADRAO)))
    except ValueError:
        dias = DIAS_PADRAO

    pessoa_momento, momento = _momento_pedido(request)
    if pessoa_momento:
        encontradas = posicao_no_momento(pessoa_momento, momento)
    else:
        encontradas = posicoes(usuarios=pessoas, dias=dias)
    recentes = [p for p in encontradas if p['recente']]

    # O atalho para o histórico segue a regra do histórico, não a do mapa.
    from core.historico import pode_ver_historico
    url_historico = reverse('core:activity_history') if pode_ver_historico(request.user) else ''

    return render(request, 'maps/mapa.html', {
        'pessoa_momento': pessoa_momento,
        'momento': momento,
        'janela_momento': JANELA_MOMENTO_MINUTOS,
        'url_historico': url_historico,
        'posicoes': encontradas,
        # O mesmo conteúdo que a API devolve, para o mapa já nascer desenhado.
        'posicoes_json': [_payload(i) for i in encontradas],
        'total': len(encontradas),
        'recentes': len(recentes),
        'ao_vivo': sum(1 for p in encontradas if p['ao_vivo']),
        'sem_posicao': (1 if pessoa_momento else pessoas.count()) - len(encontradas),
        'setores': Sector.objects.all().order_by('name'),
        'pessoas': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'filtros': filtros,
        'dias': dias,
        'fresco_minutos': FRESCO_MINUTOS,
        'coleta_ativa': ConfiguracaoMapa.carregar().coleta_ativa,
        'agora': timezone.localtime(),
        # O caminho do mapa base servido pelo próprio portal — a tela cai para
        # ele quando os provedores públicos não respondem na rede de quem abriu.
        'url_tiles': reverse('maps:tile', args=[0, 0, 0]).replace(
            '/0/0/0.png', '/{z}/{x}/{y}.png'),
    })


@login_required
def tile(request, z, x, y):
    """Um quadradinho do mapa base, buscado e guardado pelo portal.

    Existe para a rede que bloqueia o servidor de imagens do OpenStreetMap: a
    tela tenta os provedores públicos primeiro e só pede aqui quando o mapa
    ficaria cinza. Restrito a quem pode ver o mapa — isto não é um proxy aberto.
    """
    if not pode_ver_mapa(request.user):
        return HttpResponse(status=403)
    if not tiles.coordenada_valida(z, x, y):
        return HttpResponse(status=404)

    imagem, tipo = tiles.buscar(z, x, y)
    if imagem is None:
        # 502: a tela entende como "esta fonte também não vai" e para de pedir.
        return HttpResponse(status=502)

    resposta = HttpResponse(imagem, content_type=tipo or 'image/png')
    resposta['Cache-Control'] = 'private, max-age=604800'
    return resposta


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

    pessoa_momento, momento = _momento_pedido(request)
    if pessoa_momento:
        itens = posicao_no_momento(pessoa_momento, momento)
    else:
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
