"""O que o agente da loja conversa com o portal, e as fotos que ficam só no cache."""
import base64
import time
from datetime import timedelta

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import timezone

from .models import AlertaCamera, Canal

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
                     'usuario': gravador.usuario, 'senha': gravador.senha},
        'abre_as': gravador.abre_as.strftime('%H:%M'),
        'fecha_as': gravador.fecha_as.strftime('%H:%M'),
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
