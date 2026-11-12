"""Avisos fora da tela: alerta importante e loja sem contato, por WhatsApp e notificação do portal.

Tudo nasce desligado (``ConfigCameras.aviso_whatsapp`` e ``aviso_offline``). O WhatsApp sai pela Evolution
(``core.evolution.enviar_texto``), a mesma dos lembretes da rotina; o destino pode ser um número ou um grupo
(``...@g.us``). Cada alerta avisa uma vez só (``avisado_em``) e cada queda de loja também (``aviso_offline_em``).
O texto nunca acusa ninguém: é uma suspeita para conferir no vídeo.
"""
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from .models import AlertaCamera, ConfigCameras, Gravador

logger = logging.getLogger(__name__)

EM_THREAD = True              # os testes desligam para rodar na hora
VIGIA_A_CADA = 120            # segundos entre as rodadas da vigia (o sinal das lojas é o relógio)
LIMPEZA_A_CADA = 6 * 3600


def _endereco(caminho):
    base = (getattr(settings, 'BASE_URL', '') or '').rstrip('/')
    return f'{base}{caminho}' if base else caminho


def _rodar(funcao, *args):
    if EM_THREAD:
        threading.Thread(target=funcao, args=args, daemon=True).start()
    else:
        funcao(*args)


def _whatsapp(destino, texto):
    from core import evolution
    numero = destino if destino.endswith('@g.us') else evolution.normalizar_numero(destino)
    ok, detalhe = evolution.enviar_texto(numero, texto, timeout=20)
    if not ok:
        logger.warning('Aviso das câmeras não saiu no WhatsApp: %s', detalhe)
    return ok


def _notificar_superadmins(titulo, mensagem, url):
    from core.models import Notification
    from users.models import User
    pessoas = User.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(hierarchy='SUPERADMIN'))
    Notification.objects.bulk_create([Notification(user=u, title=titulo[:200], message=mensagem, notification_type='SYSTEM',
                                                   related_url=url) for u in pessoas])


def texto_do_alerta(alerta):
    local = timezone.localtime(alerta.criado_em)
    linhas = [f'Câmeras · {alerta.gravador.loja}', f'{alerta.get_tipo_display()} às {local:%H:%M} de {local:%d/%m}'
              + (f' ({alerta.canal.titulo})' if alerta.canal else ''),
              f'Prioridade {alerta.prioridade}' + (f' · IA: risco {alerta.ia_risco}%' if alerta.ia_risco is not None else '')]
    if alerta.ia_resumo:
        linhas.append(alerta.ia_resumo[:300])
    linhas.append('É uma suspeita para conferir no vídeo, não uma acusação.')
    linhas.append(_endereco(reverse('cameras:alerta_detalhe', args=[alerta.id])))
    return '\n'.join(linhas)


def avisar_alerta(alerta_id):
    """Avisa uma vez o alerta que passou da prioridade configurada. Devolve True se avisou."""
    config = ConfigCameras.atual()
    if not config.ativo or not config.aviso_whatsapp:
        return False
    alerta = AlertaCamera.objects.select_related('gravador', 'canal').filter(pk=alerta_id).first()
    if (alerta is None or alerta.avisado_em or alerta.status not in ('NOVO', 'ANALISE')
            or alerta.prioridade < config.aviso_prioridade):
        return False
    # Marca antes de mandar, numa operação só: dois processos não avisam o mesmo alerta.
    if not AlertaCamera.objects.filter(pk=alerta.pk, avisado_em__isnull=True).update(avisado_em=timezone.now()):
        return False
    texto = texto_do_alerta(alerta)
    url = reverse('cameras:alerta_detalhe', args=[alerta.id])

    def enviar():
        try:
            _notificar_superadmins(f'Câmeras: {alerta.get_tipo_display()} em {alerta.gravador.loja}', texto, url)
            if config.aviso_destino:
                _whatsapp(config.aviso_destino, texto)
        except Exception:  # noqa: BLE001 — aviso é extra: nunca derruba quem chamou
            logger.exception('Aviso do alerta %s falhou', alerta.id)
    _rodar(enviar)
    return True


def _em_horario(gravador, agora):
    local = timezone.localtime(agora).time()
    return gravador.abre_as <= local < gravador.fecha_as


def lojas_sem_contato(config, agora=None):
    """(caíram, voltaram): lojas com agente que pararam de responder no horário de funcionamento, e as que voltaram."""
    agora = agora or timezone.now()
    limite = agora - timedelta(minutes=max(5, config.aviso_offline_minutos))
    com_agente = Gravador.objects.filter(tem_equipamento=True).exclude(token_hash='').exclude(ultimo_contato__isnull=True)
    cairam = [g for g in com_agente.filter(ultimo_contato__lt=limite)
              if (g.aviso_offline_em is None or g.aviso_offline_em < g.ultimo_contato) and _em_horario(g, agora)]
    voltaram = list(com_agente.filter(ultimo_contato__gte=limite, aviso_offline_em__isnull=False,
                                      ultimo_contato__gt=F('aviso_offline_em')))
    return cairam, voltaram


def vigiar_lojas(agora=None):
    """Uma rodada: avisa queda e volta das lojas. Devolve (quantas caíram, quantas voltaram)."""
    config = ConfigCameras.atual()
    if not config.ativo or not config.aviso_offline:
        return 0, 0
    agora = agora or timezone.now()
    cairam, voltaram = lojas_sem_contato(config, agora)
    textos = []
    for g in cairam:
        if Gravador.objects.filter(pk=g.pk, ultimo_contato=g.ultimo_contato).update(aviso_offline_em=agora):
            local = timezone.localtime(g.ultimo_contato)
            textos.append(f'Câmeras · {g.loja}: agente sem contato desde {local:%H:%M} de {local:%d/%m}. '
                          'Confira se o computador da loja está ligado e com internet.')
    for g in voltaram:
        if Gravador.objects.filter(pk=g.pk).update(aviso_offline_em=None):
            textos.append(f'Câmeras · {g.loja}: o agente voltou a responder.')
    if textos:
        url = reverse('cameras:configuracao')
        texto = '\n\n'.join(textos)

        def enviar():
            try:
                _notificar_superadmins('Câmeras: lojas sem contato', texto, url)
                if config.aviso_destino:
                    _whatsapp(config.aviso_destino, texto)
            except Exception:  # noqa: BLE001
                logger.exception('Aviso de lojas sem contato falhou')
        _rodar(enviar)
    return len(cairam), len(voltaram)


def rotina(agora=None):
    """Chamada no sinal das lojas, no máximo a cada VIGIA_A_CADA: vigia, vídeos atrasados e limpeza dos vencidos."""
    if not cache.add('cameras:vigia', 1, VIGIA_A_CADA):
        return False
    from . import ia, servicos

    def tarefas():
        try:
            vigiar_lojas(agora)
            for alerta_id in servicos.vencer_videos_atrasados():
                ia.disparar(alerta_id)
            if cache.add('cameras:limpeza', 1, LIMPEZA_A_CADA):
                servicos.limpar_videos_antigos()
        except Exception:  # noqa: BLE001
            logger.exception('Rotina das câmeras falhou')
        finally:
            if EM_THREAD:
                from django.db import close_old_connections
                close_old_connections()
    _rodar(tarefas)
    return True
