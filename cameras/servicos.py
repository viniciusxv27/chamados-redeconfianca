"""O que o agente da loja conversa com o portal, e as fotos que ficam só no cache."""
import base64
import ipaddress
import json
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

from . import aprendizado
from .models import AlertaCamera, Canal, EventoLoja, Gravador, SessaoAoVivo, _caminho_quadro

FOTO_VALIDADE = 300        # segundos no cache: é imagem ao vivo, não arquivo
FOTO_TAMANHO_MAX = 400 * 1024
ALERTA_AGRUPA_MINUTOS = 5
# Quanto tempo um caso repetido soma no mesmo alerta, por tipo. Pessoa com a loja fechada é um caso só
# mesmo que várias câmeras a vejam: agrupa pela loja, não pela câmera.
AGRUPA_MINUTOS = {'ITEM_SUMIU': 5, 'FORA_HORARIO': 45, 'PERMANENCIA': 30}
AGRUPA_PELA_LOJA = ('FORA_HORARIO',)
SAUDE_TAMANHO_MAX = 12000
ALERTAS_POR_HORA = 60      # agente com defeito não pode encher a tela de alerta
SINAL_NORMAL = 30
SINAL_ASSISTINDO = 10
SINAL_FOCO = 3
VENDO_VALIDADE = 45
FOCO_VALIDADE = 20
# O vídeo do alerta: 1min30 antes e 1min30 depois do acontecimento, gravado no computador da loja.
VIDEO_ANTES = 90
VIDEO_DEPOIS = 90
VIDEO_TAMANHO_MAX = 150 * 1024 * 1024
VIDEO_ESPERA_MINUTOS = 20      # sem o vídeo até aqui (agente caiu, internet), a IA olha só a foto
QUADROS_MAX = 12
QUADRO_TAMANHO_MAX = 600 * 1024
DETALHES_MAX = 30
# Por quanto tempo o vídeo fica guardado (imagem de pessoa é dado pessoal: só o necessário).
VIDEO_DIAS = {'FALSO': 30, 'NOVO': 90, 'ANALISE': 90, 'CONFIRMADO': 365}


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


def resposta_do_sinal(gravador, canais, config=None):
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
        # O que as decisões de furto confirmado/falso alarme ensinaram, por câmera e tipo de alerta.
        'ajustes': aprendizado.ajustes_do_gravador(gravador, [c for c in canais if c.ativo]),
        'video': {'antes': VIDEO_ANTES, 'depois': VIDEO_DEPOIS},
        # O relógio do portal: o agente corrige o do computador da loja (hora errada vira medição na hora errada).
        'agora': timezone.now().isoformat(),
        'tolerancia': gravador.tolerancia_horario,
        'alertas': {'FORA_HORARIO': gravador.alerta_fora_horario, 'PERMANENCIA': gravador.alerta_permanencia,
                    'ITEM_SUMIU': True},
        'atualizacao': atualizacao_para(gravador, config),
    }


def _versao(texto):
    try:
        return tuple(int(p) for p in str(texto or '').split('.')[:3])
    except ValueError:
        return ()


def atualizacao_para(gravador, config):
    """Com a atualização automática ligada e um .exe mais novo publicado, diz ao agente onde baixar e o que conferir."""
    if (config is None or not config.atualizar_agentes or not config.instalador_arquivo or not config.instalador_sha256
            or not _versao(config.instalador_versao) or _versao(gravador.versao_agente) >= _versao(config.instalador_versao)):
        return None
    # Agente antigo não sabe se atualizar sozinho: só a partir do 3.1.
    if _versao(gravador.versao_agente) < (3, 1):
        return None
    try:
        if settings.STORAGES['default']['BACKEND'].endswith('FileSystemStorage'):
            url = default_storage.url(config.instalador_arquivo)
        else:
            url = link_do_instalador(config.instalador_arquivo, f'AgenteCameras-{config.instalador_versao}.exe')
    except Exception:  # noqa: BLE001 — sem link nesta volta; tenta no próximo sinal
        return None
    return {'versao': config.instalador_versao, 'url': url, 'sha256': config.instalador_sha256,
            'tamanho': config.instalador_tamanho}


def _limpar_valor(valor, nivel=0):
    if isinstance(valor, bool) or valor is None:
        return valor
    if isinstance(valor, (int, float)):
        return round(valor, 2) if isinstance(valor, float) else valor
    if isinstance(valor, dict) and nivel < 3:
        return {str(k)[:40]: _limpar_valor(v, nivel + 1) for k, v in list(valor.items())[:70]}
    if isinstance(valor, list) and nivel < 3:
        return [_limpar_valor(v, nivel + 1) for v in valor[:20]]
    return str(valor)[:160]


def registrar_saude(gravador, texto):
    """Guarda o retrato de saúde que o agente manda no sinal (câmeras com imagem, detector, filas, disco)."""
    if not texto or len(texto) > SAUDE_TAMANHO_MAX:
        return False
    try:
        dados = json.loads(texto)
    except ValueError:
        return False
    if not isinstance(dados, dict):
        return False
    Gravador.objects.filter(pk=gravador.pk).update(saude=_limpar_valor(dados), saude_em=timezone.now())
    return True


def problemas_de_saude(gravador):
    """[(nível, texto)] do que precisa de atenção, a partir da saúde recente. Nível: 'erro' ou 'atencao'."""
    saude = gravador.saude_atual
    if not saude:
        return []
    saida = []
    cameras = saude.get('cameras') if isinstance(saude.get('cameras'), dict) else {}
    nomes = {str(c.numero): c.titulo for c in gravador.canais.all()} if cameras else {}
    sem_imagem = [nomes.get(n, f'Câmera {n}') for n, c in cameras.items() if isinstance(c, dict) and not c.get('ok')]
    if sem_imagem:
        saida.append(('erro', f'Sem imagem: {", ".join(sem_imagem[:6])}' + ('…' if len(sem_imagem) > 6 else '')))
    relogio = saude.get('relogio_diferenca_s')
    if isinstance(relogio, (int, float)) and abs(relogio) > 120:
        saida.append(('atencao', f'Relógio do computador {abs(relogio) / 60:.0f} min {"adiantado" if relogio > 0 else "atrasado"} (o agente corrige)'))
    disco = saude.get('disco_livre_gb')
    if isinstance(disco, (int, float)) and disco < 5:
        saida.append(('erro' if disco < 2 else 'atencao', f'Pouco espaço em disco: {disco:.1f} GB'))
    fila = saude.get('fila_clipes')
    if isinstance(fila, int) and fila >= 5:
        saida.append(('atencao', f'{fila} vídeos de alerta esperando para subir'))
    if saude.get('analise') is False:
        saida.append(('atencao', 'Análise desligada neste computador'))
    cpu = saude.get('cpu')
    if isinstance(cpu, (int, float)) and cpu >= 90:
        saida.append(('atencao', f'Processador em {cpu:.0f}%'))
    return saida


class AlertaRecusado(Exception):
    pass


class AlertaIgnorado(Exception):
    """Alerta que a loja não quer (tipo desligado, equipe abrindo ou fechando): o agente não deve reenviar."""


def limpar_detalhes(bruto):
    """O que o agente mediu: só chave curta e valor simples."""
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto) if len(bruto) <= 4000 else {}
        except ValueError:
            bruto = {}
    if not isinstance(bruto, dict):
        return {}
    saida = {}
    for chave, valor in list(bruto.items())[:DETALHES_MAX]:
        chave = str(chave)[:40]
        if chave.startswith('_'):
            continue
        if isinstance(valor, bool) or valor is None:
            saida[chave] = valor
        elif isinstance(valor, (int, float)):
            saida[chave] = round(valor, 3) if isinstance(valor, float) else valor
        else:
            saida[chave] = str(valor)[:120]
    return saida


def registrar_alerta(gravador, numero_canal, tipo, confianca, motivo, foto=None, detalhes=None, grava_video=False):
    """Repetição do mesmo caso soma no alerta aberto, para o SUPERADMIN ver um caso e não vinte.

    Devolve (alerta, quer_video): quer_video diz ao agente para mandar os 3 minutos deste alerta.
    """
    if tipo not in AlertaCamera.Tipo.values:
        raise AlertaRecusado('Tipo de alerta desconhecido.')
    agora = timezone.now()
    if not gravador.alerta_ligado(tipo):
        raise AlertaIgnorado('Este tipo de alerta está desligado nesta loja.')
    if tipo == AlertaCamera.Tipo.FORA_HORARIO and gravador.na_tolerancia(agora):
        raise AlertaIgnorado('Equipe abrindo ou fechando a loja (dentro da tolerância do horário).')
    if AlertaCamera.objects.filter(gravador=gravador, criado_em__gte=agora - timedelta(hours=1)).count() >= ALERTAS_POR_HORA:
        raise AlertaRecusado('Limite de alertas por hora atingido.')
    canal = Canal.objects.filter(gravador=gravador, numero=numero_canal).first()
    janela = AGRUPA_MINUTOS.get(tipo, ALERTA_AGRUPA_MINUTOS)
    parecidos = AlertaCamera.objects.filter(gravador=gravador, tipo=tipo, status=AlertaCamera.Status.NOVO,
                                            ultimo_em__gte=agora - timedelta(minutes=janela))
    if tipo not in AGRUPA_PELA_LOJA:
        parecidos = parecidos.filter(canal=canal)
    aberto = parecidos.order_by('-criado_em').first()
    if aberto:
        aberto.repeticoes += 1
        aberto.ultimo_em = agora
        aberto.confianca = max(aberto.confianca, confianca)
        campos = ['repeticoes', 'ultimo_em', 'confianca', 'prioridade']
        quer_video = grava_video and aberto.video_status == AlertaCamera.Video.SEM_VIDEO
        if quer_video:
            aberto.video_status = AlertaCamera.Video.AGUARDANDO
            campos.append('video_status')
        aberto.prioridade = aprendizado.prioridade(aberto)
        aberto.save(update_fields=campos)
        return aberto, quer_video
    alerta = AlertaCamera(gravador=gravador, canal=canal, tipo=tipo, confianca=confianca, motivo=motivo[:255], ultimo_em=agora,
                          detalhes=limpar_detalhes(detalhes),
                          video_status=AlertaCamera.Video.AGUARDANDO if grava_video else AlertaCamera.Video.SEM_VIDEO)
    aprendizado.aplicar(alerta)
    if foto:
        alerta.foto.save('alerta.jpg', ContentFile(foto), save=False)
    alerta.save()
    return alerta, grava_video


class ClipeRecusado(Exception):
    pass


def registrar_clipe(alerta, video, quadros, inicio=None, segundos=0, evento_segundo=None):
    """Guarda os 3 minutos e os quadros-chave do alerta. `quadros`: [(segundo, bytes jpg)]. False = já tinha."""
    if alerta.video_status == AlertaCamera.Video.RECEBIDO and alerta.video:
        return False                                   # reenvio: já está aqui
    if video is None or not 0 < video.size <= VIDEO_TAMANHO_MAX:
        raise ClipeRecusado('Vídeo ausente ou grande demais.')
    cabeca = video.read(12)
    video.seek(0)
    if cabeca[4:8] != b'ftyp':
        raise ClipeRecusado('O vídeo precisa ser MP4.')
    storage = AlertaCamera._meta.get_field('video').storage
    guardados = []
    for segundo, conteudo in sorted(quadros or [], key=lambda q: q[0])[:QUADROS_MAX]:
        if conteudo[:2] != b'\xff\xd8' or len(conteudo) > QUADRO_TAMANHO_MAX:
            continue
        guardados.append({'arquivo': storage.save(_caminho_quadro(alerta, 'q.jpg'), ContentFile(conteudo)),
                          'segundo': max(-600, min(600, int(segundo)))})
    alerta.video.save('clipe.mp4', video, save=False)
    alerta.video_status = AlertaCamera.Video.RECEBIDO
    alerta.video_tamanho = video.size
    alerta.video_segundos = max(0, min(3600, int(segundos or 0)))
    evento = VIDEO_ANTES if evento_segundo is None else int(evento_segundo)
    alerta.video_evento_segundo = max(0, min(alerta.video_segundos or 3600, evento))
    alerta.video_inicio = inicio
    alerta.quadros = guardados
    alerta.save(update_fields=['video', 'video_status', 'video_tamanho', 'video_segundos', 'video_evento_segundo',
                               'video_inicio', 'quadros'])
    return True


def vencer_videos_atrasados():
    """Alerta esperando vídeo há muito tempo: desiste do vídeo e a IA olha a foto. Devolve os ids."""
    limite = timezone.now() - timedelta(minutes=VIDEO_ESPERA_MINUTOS)
    ids = list(AlertaCamera.objects.filter(video_status=AlertaCamera.Video.AGUARDANDO, criado_em__lt=limite)
               .values_list('id', flat=True)[:50])
    if ids:
        AlertaCamera.objects.filter(id__in=ids).update(video_status=AlertaCamera.Video.SEM_VIDEO)
    return ids


def _apagar_midia(alerta):
    storage = AlertaCamera._meta.get_field('video').storage
    for q in alerta.quadros or []:
        try:
            storage.delete(q.get('arquivo', ''))
        except Exception:  # noqa: BLE001 — arquivo já sumiu do bucket
            pass
    if alerta.video:
        try:
            alerta.video.delete(save=False)
        except Exception:  # noqa: BLE001
            pass


def apagar_alertas(alertas):
    """Apaga os alertas e tudo o que guardam no armazenamento (foto, vídeo e quadros). Devolve quantos."""
    apagados = 0
    for alerta in alertas.iterator(chunk_size=200):
        _apagar_midia(alerta)
        if alerta.foto:
            try:
                alerta.foto.delete(save=False)
            except Exception:  # noqa: BLE001 — arquivo já sumiu do bucket
                pass
        AlertaCamera.objects.filter(pk=alerta.pk).delete()
        apagados += 1
    if apagados:
        aprendizado.esquecer()
    return apagados


def limpar_videos_antigos(agora=None):
    """Tarefa diária: apaga vídeo e quadros vencidos (a foto, a análise e a decisão ficam). Devolve quantos."""
    agora = agora or timezone.now()
    apagados = 0
    for status, dias in VIDEO_DIAS.items():
        vencidos = AlertaCamera.objects.filter(status=status, video_status=AlertaCamera.Video.RECEBIDO,
                                               criado_em__lt=agora - timedelta(days=dias))
        for alerta in vencidos.iterator():
            _apagar_midia(alerta)
            AlertaCamera.objects.filter(pk=alerta.pk).update(video='', quadros=[], video_status=AlertaCamera.Video.APAGADO)
            apagados += 1
    return apagados


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

