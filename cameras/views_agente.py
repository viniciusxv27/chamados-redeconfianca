"""API do agente da loja. Sem sessão: cada gravador tem o seu token e só enxerga o que é dele."""
import json
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
    campos = {'ultimo_contato': timezone.now(), 'versao_agente': request.POST.get('versao', '')[:30]}
    # O agente respondeu: o que o instalador avisou ficou para trás.
    if gravador.erro_agente.startswith('Instalador:'):
        campos['erro_agente'] = gravador.erro_agente = ''
    aviso = request.POST.get('aviso', '').strip()[:255]
    if aviso:
        campos['erro_agente'] = gravador.erro_agente = aviso
    Gravador.objects.filter(pk=gravador.pk).update(**campos)
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


@_do_agente
def receber_metricas(request, gravador):
    try:
        corpo = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'erro': 'JSON inválido.'}, status=400)
    eventos = corpo.get('eventos') if isinstance(corpo, dict) else None
    if not isinstance(eventos, list):
        return JsonResponse({'erro': 'Faltam os eventos.'}, status=400)
    return JsonResponse({'ok': True, 'gravados': servicos.registrar_eventos(gravador, eventos)})


@csrf_exempt
@require_POST
def ativar(request):
    """O instalador troca o código do nome do arquivo pelo token da loja. Vale até vencer: dá para reinstalar."""
    codigo = request.POST.get('codigo', '').strip().upper()
    gravador = Gravador.objects.filter(codigo_hash=Gravador.resumo(codigo), codigo_validade__gt=timezone.now(),
                                       tem_equipamento=True).first() if codigo else None
    if gravador is None:
        return JsonResponse({'erro': 'Código inválido ou vencido.'}, status=403)
    return JsonResponse({'token': gravador.gerar_token(), 'loja': gravador.loja})

