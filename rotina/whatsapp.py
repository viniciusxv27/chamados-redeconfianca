"""Avisos da Rotina Gerencial pelo WhatsApp.

O lembrete na tela e no sino depende do portal aberto: quem dispara é o
navegador (templates/rotina/_notificador.html). O WhatsApp serve justamente para
quando o portal está fechado, então quem dispara aqui é o servidor — uma
varredura a cada minuto em cada worker do gunicorn, o mesmo arranjo das
transcrições da agenda (agenda/processamento.py). Produção roda só gunicorn:
não há cron, celery nem processo separado.

Regras:
- Os mesmos momentos dos avisos da tela, mas uma mensagem só por atividade e
  dia: o lembrete, MINUTOS_LEMBRETE antes do início. Se ele não saiu (atividade
  criada ou movida em cima da hora, servidor reiniciando), vai o de início, até
  JANELA_AVISO depois de começar. Nunca os dois.
- Só para rotina ativa com "Avisar por WhatsApp" ligado, pessoa ativa e com
  telefone no cadastro.
- Reivindicação atômica: a linha de AvisoWhatsApp (única por atividade e dia) é
  gravada ANTES do envio. O worker que não consegue gravar não manda nada — nem
  duas abas, nem três workers, nem a varredura repetida mandam duas vezes.
- Falha no envio fica registrada e não é repetida: melhor perder um lembrete do
  que mandar dois.
- Em processo de teste a varredura não liga sozinha, e o próprio
  `enviar_texto` já se recusa a mandar.
"""
import logging
import math
import os
import random
import sys
import threading
import time
from datetime import time as hora

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from . import servicos
from .models import AtividadeRotina, AvisoWhatsApp, TipoAviso

logger = logging.getLogger(__name__)

INTERVALO_VARREDURA = 60          # segundos entre uma passada e outra, em cada worker

_thread_varredura = None
_trava_varredura = threading.Lock()


# ---------------------------------------------------------------------------
# A mensagem
# ---------------------------------------------------------------------------
def link_da_atividade(atividade):
    base = (getattr(settings, 'BASE_URL', '') or '').rstrip('/')
    return base + servicos.url_da_atividade(atividade)


def tipo_do_momento(atividade, momento):
    """Antes de começar é lembrete; depois, só o aviso de início."""
    inicio = servicos._instante(momento.date(), atividade.inicio)
    return TipoAviso.LEMBRETE if momento < inicio else TipoAviso.INICIO


def texto_da_mensagem(atividade, tipo, momento):
    """Curta, no mesmo tom do sino: quando, o quê, o horário e o link direto."""
    if tipo == TipoAviso.LEMBRETE:
        inicio = servicos._instante(momento.date(), atividade.inicio)
        faltam = max(1, math.ceil((inicio - momento).total_seconds() / 60))
        cabeca = f'⏰ Em {faltam} min: *{atividade.titulo}*'
    else:
        cabeca = f'🔔 Agora: *{atividade.titulo}*'
    return (f'{cabeca}\n'
            f'{atividade.horario} · Rotina gerencial\n'
            f'Abrir: {link_da_atividade(atividade)}')


# ---------------------------------------------------------------------------
# Quem recebe agora
# ---------------------------------------------------------------------------
def pendentes(momento):
    """As atividades que pedem WhatsApp neste instante e ainda não tiveram — numa consulta.

    Na janela: do lembrete (começa em até MINUTOS_LEMBRETE) até JANELA_AVISO
    depois do início, sem ter terminado. As atividades vão de 05:00 a 23:00,
    então a janela nunca atravessa a meia-noite.
    """
    hoje = momento.date()
    desde = max(momento - servicos.JANELA_AVISO, servicos._instante(hoje, hora.min))
    ate = momento + servicos.ANTECEDENCIA_LEMBRETE
    if ate.date() != hoje:
        ate = servicos._instante(hoje, hora.max)
    ja_teve = AvisoWhatsApp.objects.filter(atividade=OuterRef('pk'), data=hoje)
    return (AtividadeRotina.objects
            .filter(dia_semana=hoje.weekday(),
                    inicio__gt=desde.time(), inicio__lte=ate.time(), fim__gt=momento.time(),
                    rotina__ativa=True, rotina__avisar_whatsapp=True, rotina__user__is_active=True)
            .exclude(rotina__user__phone='')
            .exclude(Exists(ja_teve))
            .select_related('rotina__user')
            .order_by('inicio', 'id'))


def reivindicar(user, atividade, data, tipo):
    """Grava o aviso do dia antes de mandar. None se outro worker (ou outra passada) já gravou."""
    try:
        with transaction.atomic():
            return AvisoWhatsApp.objects.create(
                user=user, atividade=atividade, data=data, tipo=tipo,
                titulo=atividade.titulo, inicio=atividade.inicio)
    except IntegrityError:
        return None


def enviar_pendentes(momento=None):
    """Uma passada: manda o WhatsApp de cada atividade que está na hora. Devolve um resumo."""
    # Pelo módulo, e não `from core.evolution import enviar_texto`: o dublê dos testes
    # troca `core.evolution.enviar_texto` e precisa valer aqui.
    from core import evolution

    momento = momento or servicos.agora()
    resumo = {'enviados': 0, 'falhas': 0, 'sem_telefone': 0, 'ja_avisados': 0}
    for atividade in pendentes(momento):
        user = atividade.rotina.user
        numero = evolution.normalizar_numero(user.phone or '')
        if not numero:
            resumo['sem_telefone'] += 1
            continue
        tipo = tipo_do_momento(atividade, momento)
        aviso = reivindicar(user, atividade, momento.date(), tipo)
        if aviso is None:
            resumo['ja_avisados'] += 1
            continue
        try:
            ok, detalhe = evolution.enviar_texto(numero, texto_da_mensagem(atividade, tipo, momento))
        except Exception as exc:                                    # noqa: BLE001 — o contrato é não levantar
            ok, detalhe = False, f'Erro inesperado: {exc}'
        AvisoWhatsApp.objects.filter(pk=aviso.pk).update(
            enviado=bool(ok), detalhe=str(detalhe or '')[:255], enviado_em=timezone.now() if ok else None)
        if ok:
            resumo['enviados'] += 1
        else:
            resumo['falhas'] += 1
            logger.warning('WhatsApp da rotina gerencial não saiu (atividade %s, pessoa %s): %s',
                           atividade.pk, user.pk, str(detalhe)[:200])
    return resumo


# ---------------------------------------------------------------------------
# A varredura em segundo plano
# ---------------------------------------------------------------------------
def em_processo_de_teste():
    try:
        from core.utils import processo_de_teste
        return processo_de_teste()
    except Exception:                                               # noqa: BLE001
        return False


def deve_rodar_varredura():
    """A varredura roda sozinha nos workers do gunicorn (produção).

    Fora dele — runserver, shell, migrate, comandos — só com
    RC_VARREDURA_ROTINA=1: o banco de dev tem telefone de gente de verdade, e um
    runserver esquecido aberto não pode sair mandando WhatsApp. Em teste, nunca.
    """
    if em_processo_de_teste():
        return False
    escolha = os.environ.get('RC_VARREDURA_ROTINA', '').strip()
    if escolha in ('0', '1'):
        return escolha == '1'
    programa = os.path.basename(sys.argv[0]) if sys.argv else ''
    return 'gunicorn' in programa


def garantir_varredura():
    """Liga a varredura deste processo, se ainda não está viva. True se ligou agora."""
    global _thread_varredura
    if not deve_rodar_varredura():
        return False
    with _trava_varredura:
        if _thread_varredura is not None and _thread_varredura.is_alive():
            return False
        _thread_varredura = threading.Thread(target=_loop_varredura, daemon=True,
                                             name='rotina-whatsapp')
        _thread_varredura.start()
    logger.info('Varredura de WhatsApp da rotina gerencial ligada no processo %s.', os.getpid())
    return True


def _loop_varredura():
    # Espalha os workers: todos subindo juntos no deploy não varrem no mesmo segundo.
    time.sleep(random.uniform(5, 20))
    while True:
        try:
            resumo = enviar_pendentes()
            if resumo['enviados'] or resumo['falhas']:
                logger.info('WhatsApp da rotina gerencial: %s', resumo)
        except Exception:                                           # noqa: BLE001
            logger.exception('Varredura de WhatsApp da rotina gerencial falhou')
        finally:
            connection.close()
        time.sleep(INTERVALO_VARREDURA + random.uniform(0, 10))
