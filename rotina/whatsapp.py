"""Avisos da Rotina Gerencial pelo WhatsApp.

O lembrete na tela e no sino depende do portal aberto: quem dispara é o
navegador (templates/rotina/_notificador.html). O WhatsApp serve justamente para
quando o portal está fechado, então quem dispara aqui é o servidor — uma
varredura a cada minuto em cada worker do gunicorn, o mesmo arranjo das
transcrições da agenda (agenda/processamento.py). Produção roda só gunicorn:
não há cron, celery nem processo separado.

Regras:
- Uma mensagem só por atividade e dia: o lembrete, no tempo escolhido em cada
  atividade (``minutos_whatsapp``: padrão de 5 minutos antes; 0 = na hora em
  que começa). Se ele não saiu (atividade criada ou movida em cima da hora,
  servidor reiniciando), vai o de início, até JANELA_AVISO depois de começar.
  Nunca os dois. O aviso na tela e no sino continua no seu próprio tempo
  (``servicos.MINUTOS_LEMBRETE``).
- Só para rotina ativa com "Avisar por WhatsApp" ligado, pessoa ativa e com
  telefone no cadastro.
- Reivindicação atômica: a linha de AvisoWhatsApp (única por atividade e dia) é
  gravada ANTES do envio. O worker que não consegue gravar não manda nada — nem
  duas abas, nem três workers, nem a varredura repetida mandam duas vezes.
- Falha no envio é tentada de novo: até MAX_TENTATIVAS vezes, com
  ESPERA_ENTRE_TENTATIVAS entre elas, enquanto a atividade estiver na janela. A
  nova tentativa também é reivindicada (UPDATE condicional), então continua uma
  mensagem só. Antes uma falha passageira perdia o lembrete do dia inteiro.
  Número que o WhatsApp diz não existir não é repetido.
- Processo sem o canal configurado (EVOLUTION_*) não reivindica aviso nenhum.
  Antes ele pegava o lembrete, falhava com "Evolution API não configurada" e
  gastava a vez de quem conseguiria mandar: entre 17 e 22/09/2026, 41 de 69
  lembretes se perderam assim, com dois servidores varrendo o mesmo banco e um
  deles sem as variáveis. Esse processo se identifica no quadro da gestão
  (CHAVE_PROCESSO_SEM_CANAL), para alguém configurar ou desligar a varredura lá.
- O número sai de ``core.telefone`` (via ``evolution.normalizar_numero``): o
  cadastro tem telefone em todo formato, e número sem conserto não é mandado.
- Cada passada deixa um recado no cache compartilhado (CHAVE_ULTIMA_VARREDURA):
  é por ele que a gestão vê se a varredura está viva em produção (``diagnostico``).
- Em processo de teste a varredura não liga sozinha, e o próprio
  `enviar_texto` já se recusa a mandar.
"""
import logging
import math
import os
import random
import socket
import sys
import threading
import time
from datetime import time as hora
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from . import servicos
from .models import MINUTOS_WHATSAPP_MAXIMO, AtividadeRotina, AvisoWhatsApp, TipoAviso

logger = logging.getLogger(__name__)

INTERVALO_VARREDURA = 60          # segundos entre uma passada e outra, em cada worker
MAX_TENTATIVAS = 3                # por atividade e dia
ESPERA_ENTRE_TENTATIVAS = timedelta(minutes=2)   # mais que o timeout do envio: não atropela quem está mandando
CHAVE_ULTIMA_VARREDURA = 'rotina:whatsapp:ultima_varredura'
CHAVE_PROCESSO_SEM_CANAL = 'rotina:whatsapp:processo_sem_canal'

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
        cabeca = f'⏰ Em {servicos.duracao_curta(faltam)}: *{atividade.titulo}*'
    else:
        cabeca = f'🔔 Agora: *{atividade.titulo}*'
    return (f'{cabeca}\n'
            f'{atividade.horario} · Rotina gerencial\n'
            f'Abrir: {link_da_atividade(atividade)}')


# ---------------------------------------------------------------------------
# Quem recebe agora
# ---------------------------------------------------------------------------
def pendentes(momento):
    """As atividades que pedem WhatsApp neste instante e ainda não tiveram.

    Na janela de cada uma: do lembrete (``minutos_whatsapp`` antes do início)
    até JANELA_AVISO depois do início, sem ter terminado. O banco traz as que
    começam em até MINUTOS_WHATSAPP_MAXIMO (poucas por vez); o tempo de cada
    atividade é conferido aqui. As atividades vão de 05:00 a 23:00 e o lembrete
    é de no máximo 2 horas: a janela nunca atravessa a meia-noite.
    """
    hoje = momento.date()
    desde = max(momento - servicos.JANELA_AVISO, servicos._instante(hoje, hora.min))
    ate = momento + timedelta(minutes=MINUTOS_WHATSAPP_MAXIMO)
    if ate.date() != hoje:
        ate = servicos._instante(hoje, hora.max)
    # Sai da lista o que já foi resolvido hoje: enviado, sem mais tentativas, ou
    # tentado há pouco (pode estar saindo agora mesmo por outro worker).
    ja_teve = AvisoWhatsApp.objects.filter(atividade=OuterRef('pk'), data=hoje).filter(
        Q(enviado=True) | Q(tentativas__gte=MAX_TENTATIVAS) | _tentado_depois_de(momento - ESPERA_ENTRE_TENTATIVAS))
    candidatas = (AtividadeRotina.objects
                  .filter(dia_semana=hoje.weekday(),
                          inicio__gt=desde.time(), inicio__lte=ate.time(), fim__gt=momento.time(),
                          rotina__ativa=True, rotina__avisar_whatsapp=True, rotina__user__is_active=True)
                  .exclude(rotina__user__phone='')
                  .exclude(Exists(ja_teve))
                  .select_related('rotina__user')
                  .order_by('inicio', 'id'))
    return [a for a in candidatas
            if momento >= servicos._instante(hoje, a.inicio) - timedelta(minutes=a.minutos_whatsapp)]


def _tentado_depois_de(limite):
    """Tentado depois de `limite` (linhas antigas, sem `tentado_em`, contam pelo registro)."""
    return Q(tentado_em__gt=limite) | Q(tentado_em__isnull=True, criado_em__gt=limite)


def reivindicar(user, atividade, data, tipo, momento=None):
    """Grava (ou pega para uma nova tentativa) o aviso do dia antes de mandar.

    None se outro worker já gravou, se já saiu, se acabaram as tentativas ou se
    a última tentativa foi agora há pouco.
    """
    momento = momento or timezone.now()
    try:
        with transaction.atomic():
            return AvisoWhatsApp.objects.create(
                user=user, atividade=atividade, data=data, tipo=tipo,
                titulo=atividade.titulo, inicio=atividade.inicio, tentativas=1, tentado_em=momento)
    except IntegrityError:
        pass
    # Já existe: vale como nova tentativa só para quem conseguir o UPDATE condicional.
    limite = momento - ESPERA_ENTRE_TENTATIVAS
    livres = (AvisoWhatsApp.objects.filter(atividade=atividade, data=data, enviado=False,
                                          tentativas__lt=MAX_TENTATIVAS)
              .exclude(_tentado_depois_de(limite)))
    aviso = livres.first()
    if aviso is None or not livres.filter(pk=aviso.pk).update(
            tentativas=F('tentativas') + 1, tentado_em=momento, tipo=tipo, detalhe=''):
        return None
    aviso.refresh_from_db()
    return aviso


def falha_definitiva(detalhe):
    """Erro que não adianta repetir: o WhatsApp respondeu que o número não existe."""
    texto = str(detalhe or '').replace(' ', '').lower()
    return '"exists":false' in texto


def faltando_no_canal():
    """O que falta configurar neste processo para o WhatsApp sair (lista vazia: pronto)."""
    return [nome for nome in ('EVOLUTION_API_URL', 'EVOLUTION_API_KEY', 'EVOLUTION_INSTANCE')
            if not (getattr(settings, nome, '') or '')]


def enviar_pendentes(momento=None):
    """Uma passada: manda o WhatsApp de cada atividade que está na hora. Devolve um resumo."""
    # Pelo módulo, e não `from core.evolution import enviar_texto`: o dublê dos testes
    # troca `core.evolution.enviar_texto` e precisa valer aqui.
    from core import evolution

    momento = momento or servicos.agora()
    resumo = {'enviados': 0, 'falhas': 0, 'sem_telefone': 0, 'ja_avisados': 0, 'novas_tentativas': 0}
    if faltando_no_canal():
        # Sem o canal, reivindicar só gastaria a vez do servidor que consegue mandar.
        resumo['sem_canal'] = True
        return resumo
    for atividade in pendentes(momento):
        user = atividade.rotina.user
        numero = evolution.normalizar_numero(user.phone or '')
        if not numero:
            resumo['sem_telefone'] += 1
            continue
        tipo = tipo_do_momento(atividade, momento)
        aviso = reivindicar(user, atividade, momento.date(), tipo, momento)
        if aviso is None:
            resumo['ja_avisados'] += 1
            continue
        if aviso.tentativas > 1:
            resumo['novas_tentativas'] += 1
        try:
            ok, detalhe = evolution.enviar_texto(numero, texto_da_mensagem(atividade, tipo, momento))
        except Exception as exc:                                    # noqa: BLE001 — o contrato é não levantar
            ok, detalhe = False, f'Erro inesperado: {exc}'
        campos = {'enviado': bool(ok), 'detalhe': str(detalhe or '')[:255],
                  'enviado_em': timezone.now() if ok else None}
        if not ok and falha_definitiva(detalhe):
            campos['tentativas'] = MAX_TENTATIVAS               # número sem WhatsApp: não adianta repetir
        AvisoWhatsApp.objects.filter(pk=aviso.pk).update(**campos)
        if ok:
            resumo['enviados'] += 1
        else:
            resumo['falhas'] += 1
            logger.warning('WhatsApp da rotina gerencial não saiu (atividade %s, pessoa %s, tentativa %s): %s',
                           atividade.pk, user.pk, aviso.tentativas, str(detalhe)[:200])
    return resumo


def registrar_passada(resumo, erro=''):
    """O recado de que a varredura está viva, no cache compartilhado entre os workers."""
    try:
        cache.set(CHAVE_ULTIMA_VARREDURA, {'em': timezone.now().isoformat(), 'pid': os.getpid(),
                                           'resumo': resumo, 'erro': str(erro)[:300]}, 6 * 3600)
    except Exception:                                               # noqa: BLE001 — diagnóstico nunca derruba o envio
        pass


def registrar_processo_sem_canal():
    """O recado para a gestão: este servidor varre sem o canal configurado (e por isso não pega nada)."""
    try:
        cache.set(CHAVE_PROCESSO_SEM_CANAL, {'em': timezone.now().isoformat(), 'pid': os.getpid(),
                                             'host': socket.gethostname(), 'faltando': faltando_no_canal()},
                  6 * 3600)
    except Exception:                                               # noqa: BLE001 — diagnóstico nunca derruba nada
        pass


def diagnostico(hoje=None):
    """O que a gestão precisa para saber por que o WhatsApp não chega.

    Canal configurado? Varredura viva (última passada, de qualquer worker)?
    O que saiu e o que falhou hoje, com o motivo? Quem tem o WhatsApp ligado,
    mas um telefone no cadastro que não dá para usar?
    """
    from core.telefone import problema
    from django.utils.dateparse import parse_datetime

    from .models import RotinaGerencial

    hoje = hoje or servicos.agora().date()
    faltando = faltando_no_canal()
    try:
        ultima = cache.get(CHAVE_ULTIMA_VARREDURA)
        sem_canal = cache.get(CHAVE_PROCESSO_SEM_CANAL)
    except Exception:                                               # noqa: BLE001
        ultima = sem_canal = None
    if ultima:
        ultima = dict(ultima, em=parse_datetime(ultima.get('em') or ''))
    if sem_canal:
        sem_canal = dict(sem_canal, em=parse_datetime(sem_canal.get('em') or ''))

    avisos = list(AvisoWhatsApp.objects.filter(data=hoje).select_related('user').order_by('-criado_em'))
    falhas = [a for a in avisos if not a.enviado and a.detalhe]
    telefones = []
    rotinas = (RotinaGerencial.objects.filter(ativa=True, avisar_whatsapp=True, user__is_active=True)
               .select_related('user').order_by('user__first_name', 'user__last_name'))
    for rotina in rotinas:
        motivo = problema(rotina.user.phone)
        if motivo:
            telefones.append({'user': rotina.user, 'telefone': rotina.user.phone, 'motivo': motivo})
    return {
        'configurado': not faltando,
        'faltando': faltando,
        'ultima_varredura': ultima,
        'processo_sem_canal': sem_canal,
        'enviados_hoje': sum(1 for a in avisos if a.enviado),
        'falhas_hoje': len(falhas),
        'falhas': falhas[:8],
        'telefones_ruins': telefones,
        'max_tentativas': MAX_TENTATIVAS,
    }


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
    avisado_sem_canal = False
    while True:
        try:
            resumo = enviar_pendentes()
            if resumo.get('sem_canal'):
                registrar_processo_sem_canal()
                if not avisado_sem_canal:
                    logger.warning('Varredura de WhatsApp da rotina sem a Evolution configurada neste processo '
                                   '(falta %s): ela não pega nenhum lembrete.', ', '.join(faltando_no_canal()))
                    avisado_sem_canal = True
            else:
                registrar_passada(resumo)
            if resumo['enviados'] or resumo['falhas']:
                logger.info('WhatsApp da rotina gerencial: %s', resumo)
        except Exception as exc:                                    # noqa: BLE001
            registrar_passada({}, erro=exc)
            logger.exception('Varredura de WhatsApp da rotina gerencial falhou')
        finally:
            connection.close()
        time.sleep(INTERVALO_VARREDURA + random.uniform(0, 10))
