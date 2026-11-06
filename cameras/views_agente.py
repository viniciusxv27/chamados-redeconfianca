"""API do agente da loja. Sem sessão: cada gravador tem o seu token e só enxerga o que é dele."""
import json
from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from django.utils.dateparse import parse_datetime

from . import ia, servicos
from .models import AlertaCamera, ConfigCameras, Gravador


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
    if 'ao_vivo' in request.POST:
        campos['ao_vivo_situacao'] = request.POST['ao_vivo'][:200]
    if 'log' in request.POST:
        campos['log_agente'], campos['log_agente_em'] = request.POST['log'][-8000:], timezone.now()
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
    # Agente 3.0 em diante grava os 3 minutos em volta do alerta e avisa com video=1.
    grava_video = request.POST.get('video') == '1'
    try:
        alerta, quer_video = servicos.registrar_alerta(gravador, canal, request.POST.get('tipo', ''), confianca,
                                                       request.POST.get('motivo', ''), foto,
                                                       detalhes=request.POST.get('detalhes'), grava_video=grava_video)
    except servicos.AlertaRecusado as erro:
        return JsonResponse({'erro': str(erro)}, status=429 if 'Limite' in str(erro) else 400)
    if alerta.repeticoes == 1 and not grava_video:
        ia.disparar(alerta.id)          # sem vídeo a caminho, a IA olha a foto agora
    return JsonResponse({'ok': True, 'alerta': alerta.id, 'repeticoes': alerta.repeticoes, 'quer_video': quer_video,
                         'antes': servicos.VIDEO_ANTES, 'depois': servicos.VIDEO_DEPOIS})


@_do_agente
def receber_clipe(request, gravador):
    """Os 3 minutos do alerta (MP4) e os quadros-chave (quadro_<segundo>.jpg). Reenviar é seguro."""
    alerta = AlertaCamera.objects.filter(pk=request.POST.get('alerta') or 0, gravador=gravador).first()
    if alerta is None:
        # O alerta não existe mais (ou é de outra loja): o agente pode jogar o arquivo fora.
        return JsonResponse({'erro': 'Alerta não encontrado.', 'descartar': True}, status=404)
    if alerta.video_status == AlertaCamera.Video.APAGADO:
        return JsonResponse({'ok': True, 'ja_tinha': True})
    quadros = []
    for campo, arquivo in request.FILES.items():
        if campo.startswith('quadro_') and arquivo.size <= servicos.QUADRO_TAMANHO_MAX:
            try:
                quadros.append((int(campo.removeprefix('quadro_').replace('m', '-')), arquivo.read()))
            except ValueError:
                continue
    inicio = parse_datetime(request.POST.get('inicio') or '')
    if inicio is not None and timezone.is_naive(inicio):
        inicio = timezone.make_aware(inicio)
    try:
        segundos = int(float(request.POST.get('segundos') or 0))
        evento = request.POST.get('evento_segundo')
        evento = int(float(evento)) if evento not in (None, '') else None
        novo = servicos.registrar_clipe(alerta, request.FILES.get('video'), quadros, inicio, segundos, evento)
    except (ValueError, servicos.ClipeRecusado) as erro:
        return JsonResponse({'erro': str(erro) or 'Dados inválidos.', 'descartar': isinstance(erro, servicos.ClipeRecusado)}, status=400)
    if novo:
        ia.disparar(alerta.id)
    return JsonResponse({'ok': True, 'ja_tinha': not novo})


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


TOKEN_NO_CACHE = 300


def _gravador_do_token_em_cache(request):
    """Esta rota é perguntada a cada poucos segundos por todas as lojas: não vai ao banco à toa."""
    cabecalho = request.headers.get('Authorization', '')
    token = cabecalho[7:].strip() if cabecalho.startswith('Bearer ') else ''
    if not token:
        return None
    resumo = Gravador.resumo(token)
    chave = f'cameras:token:{resumo}'
    gravador_id = cache.get(chave)
    if gravador_id is None:
        gravador_id = Gravador.objects.filter(token_hash=resumo, tem_equipamento=True).values_list('id', flat=True).first()
        if gravador_id:
            cache.set(chave, gravador_id, TOKEN_NO_CACHE)
    return gravador_id


@csrf_exempt
@require_POST
def ao_vivo_pedidos(request):
    gravador_id = _gravador_do_token_em_cache(request)
    if not gravador_id:
        return JsonResponse({'erro': 'Token inválido.'}, status=401)
    try:
        corpo = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'erro': 'JSON inválido.'}, status=400)
    return JsonResponse(servicos.pedidos_para_o_agente(gravador_id, corpo.get('ativas') if isinstance(corpo, dict) else None))


@csrf_exempt
@require_POST
def ao_vivo_resposta(request):
    gravador_id = _gravador_do_token_em_cache(request)
    if not gravador_id:
        return JsonResponse({'erro': 'Token inválido.'}, status=401)
    try:
        corpo = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'erro': 'JSON inválido.'}, status=400)
    if not isinstance(corpo, dict) or not servicos.resposta_do_agente(gravador_id, corpo):
        return JsonResponse({'erro': 'Sessão inválida.'}, status=400)
    return JsonResponse({'ok': True})

