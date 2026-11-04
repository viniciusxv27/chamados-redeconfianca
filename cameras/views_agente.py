"""API do agente da loja. Sem sessão: cada gravador tem o seu token e só enxerga o que é dele."""
from functools import wraps

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import servicos
from .models import ConfigCameras, Gravador


def _do_token(request):
    cabecalho = request.headers.get('Authorization', '')
    if not cabecalho.startswith('Bearer ') or not cabecalho[7:].strip():
        return None
    return Gravador.objects.filter(token_hash=Gravador.resumo(cabecalho[7:].strip()), tem_equipamento=True).first()


def _do_agente(view):
    @csrf_exempt
    @require_POST
    @wraps(view)
    def _view(request):
        gravador = _do_token(request)
        if gravador is None:
            return JsonResponse({'erro': 'Token inválido.'}, status=401)
        # Desligado: responde sem imagem nenhuma, nem senha, e o agente fica só esperando.
        if not ConfigCameras.atual().ativo:
            return JsonResponse({'ativo': False, 'proximo_sinal': servicos.SINAL_NORMAL})
        return view(request, gravador)
    return _view


@_do_agente
def sinal(request, gravador):
    Gravador.objects.filter(pk=gravador.pk).update(ultimo_contato=timezone.now(),
                                                   versao_agente=request.POST.get('versao', '')[:30])
    return JsonResponse(servicos.resposta_do_sinal(gravador, list(gravador.canais.all())))


@_do_agente
def receber_fotos(request, gravador):
    canais = {str(c.numero): c.id for c in gravador.canais.filter(ativo=True)}
    guardadas = 0
    for campo, arquivo in request.FILES.items():
        canal_id = canais.get(campo.removeprefix('canal_'))
        if canal_id and arquivo.size <= servicos.FOTO_TAMANHO_MAX:
            servicos.guardar_foto(canal_id, arquivo.read())
            guardadas += 1
    return JsonResponse({'ok': True, 'guardadas': guardadas})


@_do_agente
def receber_alerta(request, gravador):
    try:
        confianca = max(0, min(100, int(request.POST.get('confianca') or 0)))
        canal = int(request.POST.get('canal') or 0)
    except ValueError:
        return JsonResponse({'erro': 'Número inválido.'}, status=400)
    arquivo = request.FILES.get('foto')
    foto = arquivo.read() if arquivo and arquivo.size <= servicos.FOTO_TAMANHO_MAX * 5 else None
    try:
        alerta = servicos.registrar_alerta(gravador, canal, request.POST.get('tipo', ''), confianca,
                                           request.POST.get('motivo', ''), foto)
    except servicos.AlertaRecusado as erro:
        return JsonResponse({'erro': str(erro)}, status=429 if 'Limite' in str(erro) else 400)
    return JsonResponse({'ok': True, 'alerta': alerta.id, 'repeticoes': alerta.repeticoes})


@_do_agente
def receber_descoberta(request, gravador):
    try:
        servicos.registrar_descoberta(gravador, request.POST)
    except servicos.DescobertaRecusada as erro:
        return JsonResponse({'erro': str(erro)}, status=400)
    return JsonResponse({'ok': True})
