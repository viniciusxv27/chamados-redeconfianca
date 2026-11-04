"""O que o agente da loja conversa com o portal, e as fotos que ficam só no cache."""
import base64
import ipaddress
import re
import time
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import AlertaCamera, Canal, EventoLoja, Gravador

FOTO_VALIDADE = 300        # segundos no cache: é imagem ao vivo, não arquivo
FOTO_TAMANHO_MAX = 400 * 1024
ALERTA_AGRUPA_MINUTOS = 5
ALERTAS_POR_HORA = 60      # agente com defeito não pode encher a tela de alerta
SINAL_NORMAL = 30
SINAL_ASSISTINDO = 10
SINAL_FOCO = 3
VENDO_VALIDADE = 45
FOCO_VALIDADE = 20


def _chave_foto(canal_id):
    return f'cameras:foto:{canal_id}'


def _chave_vendo(gravador_id):
    return f'cameras:vendo:{gravador_id}'


def _chave_foco(gravador_id):
    return f'cameras:foco:{gravador_id}'


CHAVE_VENDO_MOSAICO = 'cameras:vendo:mosaico'


def marcar_vendo_mosaico():
    cache.set(CHAVE_VENDO_MOSAICO, 1, VENDO_VALIDADE)


def marcar_vendo_loja(gravador_id, foco=None):
    cache.set(_chave_vendo(gravador_id), 1, VENDO_VALIDADE)
    if foco:
        cache.set(_chave_foco(gravador_id), foco, FOCO_VALIDADE)


def guardar_foto(canal_id, conteudo):
    cache.set(_chave_foto(canal_id), {'b64': base64.b64encode(conteudo).decode(), 'em': time.time()}, FOTO_VALIDADE)


def fotos_dos_canais(ids, desde=0):
    """{canal_id: {'b64', 'em'}} só do que mudou: uma resposta só, em vez de um pedido por imagem."""
    chaves = {_chave_foto(i): i for i in ids}
    return {chaves[c]: foto for c, foto in cache.get_many(list(chaves)).items() if foto and foto['em'] > desde}


def resposta_do_sinal(gravador, canais):
    """O que o agente deve fazer agora. Só manda foto quando tem alguém olhando: poupa a internet da loja."""
    ativos = [c for c in canais if c.ativo]
    foco = cache.get(_chave_foco(gravador.id))
    if cache.get(_chave_vendo(gravador.id)):
        fotos, proximo = [c.numero for c in ativos], SINAL_ASSISTINDO
    elif cache.get(CHAVE_VENDO_MOSAICO):
        fotos, proximo = [c.numero for c in ativos[:1]], SINAL_ASSISTINDO
    else:
        fotos, proximo = [], SINAL_NORMAL
    return {
        'ativo': True,
        'proximo_sinal': SINAL_FOCO if foco else proximo,
        'fotos': fotos,
        'foco': foco,
        'gravador': {'ip': gravador.ip_local, 'porta': gravador.porta_rtsp, 'caminho': gravador.caminho_rtsp,
                     'serial': gravador.serial,
                     'usuario': gravador.usuario, 'senha': gravador.senha},
        'abre_as': gravador.abre_as.strftime('%H:%M'),
        'fecha_as': gravador.fecha_as.strftime('%H:%M'),
        'matinal': {'inicio': gravador.matinal_inicio.strftime('%H:%M'), 'fim': gravador.matinal_fim.strftime('%H:%M'),
                    'minimo': gravador.matinal_minutos * 60},
        'canais': [{'numero': c.numero, 'ia': c.ia_ativa, 'zonas': c.zonas} for c in canais if c.ativo],
    }


class AlertaRecusado(Exception):
    pass


def registrar_alerta(gravador, numero_canal, tipo, confianca, motivo, foto=None):
    """Repetição do mesmo caso soma no alerta aberto, para o SUPERADMIN ver um caso e não vinte."""
    if tipo not in AlertaCamera.Tipo.values:
        raise AlertaRecusado('Tipo de alerta desconhecido.')
    agora = timezone.now()
    if AlertaCamera.objects.filter(gravador=gravador, criado_em__gte=agora - timedelta(hours=1)).count() >= ALERTAS_POR_HORA:
        raise AlertaRecusado('Limite de alertas por hora atingido.')
    canal = Canal.objects.filter(gravador=gravador, numero=numero_canal).first()
    aberto = (AlertaCamera.objects.filter(gravador=gravador, canal=canal, tipo=tipo, status=AlertaCamera.Status.NOVO,
                                          ultimo_em__gte=agora - timedelta(minutes=ALERTA_AGRUPA_MINUTOS))
              .order_by('-criado_em').first())
    if aberto:
        aberto.repeticoes += 1
        aberto.ultimo_em = agora
        aberto.confianca = max(aberto.confianca, confianca)
        aberto.save(update_fields=['repeticoes', 'ultimo_em', 'confianca'])
        return aberto
    alerta = AlertaCamera(gravador=gravador, canal=canal, tipo=tipo, confianca=confianca, motivo=motivo[:255], ultimo_em=agora)
    if foto:
        alerta.foto.save('alerta.jpg', ContentFile(foto), save=False)
    alerta.save()
    return alerta


class DescobertaRecusada(Exception):
    pass


def registrar_descoberta(gravador, dados):
    """Guarda o que o agente achou. O IP vira parte do endereço com a senha: só rede interna, e o caminho é conferido."""
    agora = timezone.now()
    erro = (dados.get('erro') or '').strip()[:255]
    if erro:
        Gravador.objects.filter(pk=gravador.pk).update(erro_agente=erro, descoberta_em=agora)
        return
    try:
        ip = ipaddress.ip_address((dados.get('ip') or '').strip())
        porta = int(dados.get('porta') or 554)
        canais = sorted({int(c) for c in (dados.get('canais') or '').split(',') if c.strip()})
    except ValueError as exc:
        raise DescobertaRecusada('Dados da descoberta inválidos.') from exc
    caminho = (dados.get('caminho') or '').strip()
    if not ip.is_private or not 0 < porta < 65536:
        raise DescobertaRecusada('O gravador precisa estar na rede interna.')
    if not caminho.startswith('/') or '{canal}' not in caminho or len(caminho) > 160:
        raise DescobertaRecusada('Caminho de vídeo inválido.')
    if not canais or canais[0] < 1 or canais[-1] > 64:
        raise DescobertaRecusada('Nenhuma câmera informada.')
    Gravador.objects.filter(pk=gravador.pk).update(
        ip_local=str(ip), porta_rtsp=porta, caminho_rtsp=caminho, erro_agente='', descoberta_em=agora,
        serial_detectado=(dados.get('serial') or '').strip()[:60], modelo_detectado=(dados.get('modelo') or '').strip()[:80])
    for numero in canais:
        Canal.objects.get_or_create(gravador=gravador, numero=numero)
    gravador.canais.filter(numero__in=canais).update(ativo=True)
    gravador.canais.exclude(numero__in=canais).update(ativo=False)


EVENTOS_POR_PEDIDO = 500
EVENTO_MAIS_ANTIGO_DIAS = 30


def _momento(texto):
    momento = parse_datetime(str(texto or ''))
    if momento is None:
        raise ValueError
    return momento if timezone.is_aware(momento) else timezone.make_aware(momento)


def registrar_eventos(gravador, eventos):
    """Grava as medições. Reenvio não conta duas vezes; item estragado cai sozinho, sem perder o lote."""
    agora = timezone.now()
    canais = {c.numero: c.id for c in gravador.canais.all()}
    novos = []
    for item in (eventos or [])[:EVENTOS_POR_PEDIDO]:
        try:
            tipo, chave = item['tipo'], str(item['chave'])[:32]
            inicio, fim = _momento(item['inicio']), _momento(item['fim'])
            duracao, pessoas = int(item.get('duracao') or 0), int(item.get('pessoas') or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if (tipo not in EventoLoja.Tipo.values or not chave or inicio > fim or duracao < 0 or pessoas < 0 or pessoas > 500
                or duracao > 86400 or fim > agora + timedelta(hours=1)
                or fim < agora - timedelta(days=EVENTO_MAIS_ANTIGO_DIAS)):
            continue
        local = timezone.localtime(fim)
        novos.append(EventoLoja(gravador=gravador, canal_id=canais.get(item.get('canal')), chave=chave, tipo=tipo,
                                zona=str(item.get('zona') or '')[:40], inicio=inicio, fim=fim, data=local.date(),
                                hora=local.hour, duracao=duracao, pessoas=pessoas))
    EventoLoja.objects.bulk_create(novos, ignore_conflicts=True)
    return len(novos)


def nome_do_instalador(gravador, codigo):
    loja = re.sub(r'[^A-Z0-9]+', '-', unicodedata.normalize('NFKD', gravador.loja).encode('ascii', 'ignore').decode().upper()).strip('-')
    return f'Instalar-Cameras-{loja or gravador.id}-{codigo}.exe'


def link_do_instalador(arquivo, nome):
    """Link temporário que baixa com o nome da loja. O bucket é público, mas é a assinatura que permite escolher o nome."""
    import boto3
    from botocore.config import Config

    chave = default_storage._normalize_name(arquivo)
    cliente = boto3.client('s3', endpoint_url=settings.AWS_S3_ENDPOINT_URL, region_name=settings.AWS_S3_REGION_NAME,
                           aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                           config=Config(signature_version='s3v4'))
    return cliente.generate_presigned_url('get_object', ExpiresIn=3600, Params={
        'Bucket': settings.AWS_STORAGE_BUCKET_NAME, 'Key': chave,
        'ResponseContentDisposition': f'attachment; filename="{nome}"', 'ResponseContentType': 'application/octet-stream'})

