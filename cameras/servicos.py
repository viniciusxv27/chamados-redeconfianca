"""O que o agente da loja conversa com o portal, e as fotos que ficam só no cache."""
import base64
import ipaddress
import re
import time
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import AlertaCamera, Canal, EventoLoja, Gravador, SessaoAoVivo

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


# ── Vídeo ao vivo ──────────────────────────────────────────────────────────────
AO_VIVO_ESPERA_PARADO = 4      # segundos entre as perguntas do agente, sem ninguém pedindo vídeo
AO_VIVO_ESPERA_ATIVO = 1
AO_VIVO_MARCA_VALIDADE = 30
AO_VIVO_SDP_MAXIMO = 20000
AO_VIVO_SESSAO_ANTIGA_HORAS = 24
AO_VIVO_PEDIDOS_POR_VEZ = 32
AO_VIVO_PEDIDO_VALE = 30       # o navegador desiste antes disso


def _chave_ao_vivo(gravador_id):
    return f'cameras:aovivo:tem:{gravador_id}'


def marcar_ao_vivo(gravador_id):
    cache.set(_chave_ao_vivo(gravador_id), 1, AO_VIVO_MARCA_VALIDADE)


def abrir_sessoes(user, pedidos):
    """[{canal, qualidade, sdp}] → [{id} ou {erro}], na mesma ordem. Só o pedido: quem atende é o agente da loja."""
    limite = timezone.now() - timedelta(hours=AO_VIVO_SESSAO_ANTIGA_HORAS)
    SessaoAoVivo.objects.filter(criada_em__lt=limite).delete()
    pedidos = (pedidos or [])[:AO_VIVO_PEDIDOS_POR_VEZ]
    ids = [p.get('canal') for p in pedidos if isinstance(p, dict)]
    canais = {c.id: c for c in Canal.objects.filter(id__in=[i for i in ids if isinstance(i, int)], ativo=True).select_related('gravador')}
    saida, lojas = [], set()
    for pedido in pedidos:
        canal = canais.get(pedido.get('canal')) if isinstance(pedido, dict) else None
        sdp = str(pedido.get('sdp') or '') if isinstance(pedido, dict) else ''
        if canal is None or not sdp or len(sdp) > AO_VIVO_SDP_MAXIMO:
            saida.append({'erro': 'Pedido inválido.'})
            continue
        if not canal.gravador.online:
            saida.append({'erro': 'Agente da loja sem contato.'})
            continue
        if not canal.gravador.tem_ao_vivo:
            saida.append({'erro': 'Reinstale o agente desta loja para ter vídeo ao vivo.', 'atualizar': True})
            continue
        qualidade = SessaoAoVivo.ALTA if pedido.get('qualidade') == SessaoAoVivo.ALTA else SessaoAoVivo.BAIXA
        sessao = SessaoAoVivo.objects.create(gravador=canal.gravador, canal=canal, user=user, qualidade=qualidade, oferta=sdp)
        marcar_ao_vivo(canal.gravador_id)
        lojas.add(canal.gravador_id)
        saida.append({'id': str(sessao.id)})
    return saida, lojas


def respostas_das_sessoes(user, ids):
    sessoes = SessaoAoVivo.objects.filter(id__in=ids, user=user).only('id', 'resposta', 'erro', 'codec', 'estado')
    return {str(s.id): {'sdp': s.resposta, 'erro': s.erro, 'codec': s.codec} for s in sessoes if s.resposta or s.erro}


def atualizar_estado_sessoes(user, ids, estado):
    if estado not in (SessaoAoVivo.CONECTADA, SessaoAoVivo.FALHOU, SessaoAoVivo.FECHADA):
        return 0
    agora = timezone.now()
    campos = {'estado': estado, 'atualizada_em': agora}
    if estado == SessaoAoVivo.CONECTADA:
        campos['conectada_em'] = agora
    feitas = SessaoAoVivo.objects.filter(id__in=ids, user=user).update(**campos)
    if estado == SessaoAoVivo.FECHADA:
        # O agente precisa saber logo: cada vídeo aberto ocupa upload da loja e uma conexão do gravador.
        for gravador_id in SessaoAoVivo.objects.filter(id__in=ids, user=user).values_list('gravador_id', flat=True).distinct():
            marcar_ao_vivo(gravador_id)
    return feitas


def pedidos_para_o_agente(gravador_id, ativas):
    """O que o agente deve abrir e fechar. Parado, responde só com o cache, sem ir ao banco."""
    ativas = [a for a in (ativas or []) if isinstance(a, str)][:64]
    if not ativas and not cache.get(_chave_ao_vivo(gravador_id)):
        return {'ofertas': [], 'fechar': [], 'proximo': AO_VIVO_ESPERA_PARADO}
    ofertas = list(SessaoAoVivo.objects.filter(gravador_id=gravador_id, estado=SessaoAoVivo.PEDIDA,
                                               criada_em__gte=timezone.now() - timedelta(seconds=AO_VIVO_PEDIDO_VALE)).select_related('canal'))
    agora = timezone.now()
    SessaoAoVivo.objects.filter(id__in=[o.id for o in ofertas]).update(estado=SessaoAoVivo.ENTREGUE, entregue_em=agora, atualizada_em=agora)
    fechar = []
    if ativas:
        try:
            abertas = {str(i) for i in SessaoAoVivo.objects.filter(id__in=ativas).exclude(
                estado__in=[SessaoAoVivo.FECHADA, SessaoAoVivo.FALHOU]).values_list('id', flat=True)}
        except (ValueError, ValidationError):
            abertas = set()
        fechar = [a for a in ativas if a not in abertas]
    return {'ofertas': [{'id': str(o.id), 'canal': o.canal.numero, 'qualidade': o.qualidade, 'sdp': o.oferta} for o in ofertas],
            'fechar': fechar, 'proximo': AO_VIVO_ESPERA_ATIVO}


def resposta_do_agente(gravador_id, dados):
    agora = timezone.now()
    campos = {'atualizada_em': agora, 'respondida_em': agora}
    if dados.get('erro'):
        campos.update(erro=str(dados['erro'])[:200])
    else:
        sdp = str(dados.get('sdp') or '')
        if not sdp or len(sdp) > AO_VIVO_SDP_MAXIMO:
            return 0
        campos.update(resposta=sdp, codec=str(dados.get('codec') or '')[:20])
    try:
        # Quem já desistiu continua fechado: a resposta atrasada não pode reabrir a sessão.
        sessao = SessaoAoVivo.objects.filter(id=dados.get('id'), gravador_id=gravador_id)
        if not sessao.filter(estado__in=[SessaoAoVivo.FALHOU, SessaoAoVivo.FECHADA]).exists():
            campos['estado'] = SessaoAoVivo.FALHOU if dados.get('erro') else SessaoAoVivo.RESPONDIDA
        return sessao.update(**campos)
    except (ValueError, ValidationError):
        return 0

