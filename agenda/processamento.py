"""Processamento das transcrições que não para até terminar.

Antes, cada transcrição rodava numa thread solta dentro do worker do gunicorn
que recebeu o "finalizar". Se o worker fosse reciclado (--max-requests), se
houvesse deploy ou se a OpenAI caísse, a transcrição ficava "Processando" para
sempre, ou virava "Erro" e alguém precisava lembrar de clicar em reprocessar.

Aqui ficam as peças que tornam o processamento durável:

1. Reivindicação atômica: um UPDATE condicional no banco decide quem processa.
   Dois workers (ou o varredor e um clique) nunca rodam a mesma transcrição.
2. Batimento: enquanto trabalha, o job renova `batimento_em`. Sem batimento há
   3 minutos, o job morreu — e qualquer worker pode retomar.
3. Nova tentativa: falha passageira (OpenAI fora, storage fora, trecho que não
   transcreveu) agenda outra tentativa com espera crescente, sem desistir cedo.
4. Varredor: a cada minuto, em cada worker, retoma o que parou, refaz o que
   está na hora de refazer e fecha gravações cujo navegador sumiu.

O pipeline em si (juntar partes, Whisper, análise, tarefas) está em
agenda/views.py (`processar_transcricao`) e salva o progresso etapa a etapa:
retomar não refaz o que já foi feito.
"""
import logging
import os
import random
import socket
import sys
import threading
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, connection
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger('agenda.transcricao')

BATIMENTO_SEGUNDOS = 45
PARADO_APOS_SEGUNDOS = 180          # sem batimento há 3 min: o job morreu
GRAVACAO_ORFA_MINUTOS = 20          # sem parte nova há 20 min: o navegador sumiu
GRAVACAO_VAZIA_HORAS = 2            # sessão aberta que nunca recebeu áudio
INTERVALO_VARREDURA = 60
MAX_TENTATIVAS = 60                 # com espera de até 30 min, ~1 dia tentando
PARTES_RETENCAO_DIAS = 7
RETOMADAS_POR_VARREDURA = 2
MAX_JOBS_POR_PROCESSO = 2

# Em processo de teste nenhum job roda sozinho: o banco de dev tem transcrições
# reais e a chave da OpenAI é de verdade. O teste liga isto dentro de um bloco
# com a OpenAI simulada, e mesmo assim só roda na hora (dentro da transação).
PERMITIR_JOBS_NOS_TESTES = False

_jobs_ativos = set()
_trava_jobs = threading.Lock()
_thread_varredura = None
_trava_varredura = threading.Lock()


class ErroPermanente(Exception):
    """Falha que uma nova tentativa não resolve: sem áudio, sem chave da OpenAI…"""


class PerdeuAReivindicacao(Exception):
    """Outro processo assumiu esta transcrição (ou a gravação foi reaberta)."""


def em_processo_de_teste():
    try:
        from core.utils import processo_de_teste
        return processo_de_teste()
    except Exception:                                               # noqa: BLE001
        return False


def deve_rodar_varredura():
    """A varredura automática roda nos workers do gunicorn (produção).

    Fora dele — runserver, shell, migrate, comandos, testes — só com
    RC_VARREDURA_TRANSCRICOES=1: o banco de dev tem transcrições reais e a
    chave da OpenAI é de verdade, e um runserver esquecido aberto não deve
    sair reprocessando nada. Em teste, nunca.
    """
    if em_processo_de_teste():
        return False
    escolha = os.environ.get('RC_VARREDURA_TRANSCRICOES', '').strip()
    if escolha in ('0', '1'):
        return escolha == '1'
    programa = os.path.basename(sys.argv[0]) if sys.argv else ''
    return 'gunicorn' in programa


def novo_dono():
    return f'{socket.gethostname()[:30]}:{os.getpid()}:{uuid.uuid4().hex[:10]}'


def backoff(tentativas):
    """Espera antes da próxima tentativa: 1, 2, 4, 8, 16 e depois 30 minutos."""
    return timedelta(minutes=min(30, 2 ** max(0, int(tentativas) - 1)))


def reivindicar(transcricao_id, dono):
    """Marca a transcrição como deste job — ou desiste, se outro está nela.

    Um único UPDATE condicional: só pega quem está em processamento, sem job
    vivo (batimento recente) e sem espera marcada para depois. No Postgres o
    UPDATE trava a linha; o segundo que chegar reavalia a condição com o
    batimento já renovado e não pega nada.
    """
    from .models import MeetingTranscription

    agora = timezone.now()
    parado = agora - timedelta(seconds=PARADO_APOS_SEGUNDOS)
    pegou = (MeetingTranscription.objects
             .filter(pk=transcricao_id, status='processing')
             .filter(Q(batimento_em__isnull=True) | Q(batimento_em__lt=parado))
             .filter(Q(proxima_tentativa_em__isnull=True) | Q(proxima_tentativa_em__lte=agora))
             .update(processando_por=dono, batimento_em=agora))
    return pegou == 1


def conferir_dono(transcricao_id, dono):
    """Levanta PerdeuAReivindicacao se a transcrição não é mais deste job.

    Chamado antes de cada gravação no banco: um job que ficou sem batimento
    (máquina lenta, rede) e foi retomado por outro não pode sobrescrever o
    resultado do outro.
    """
    if not dono:
        return
    from .models import MeetingTranscription

    atual = (MeetingTranscription.objects.filter(pk=transcricao_id)
             .values_list('processando_por', 'status').first())
    if atual is None or atual[0] != dono or atual[1] != 'processing':
        raise PerdeuAReivindicacao()


class Batimento:
    """Renova `batimento_em` a cada 45 s enquanto o job trabalha (numa thread)."""

    def __init__(self, transcricao_id, dono, intervalo=BATIMENTO_SEGUNDOS):
        self.transcricao_id = transcricao_id
        self.dono = dono
        self.intervalo = intervalo
        self._parar = threading.Event()
        self._thread = None

    def _bater(self):
        from .models import MeetingTranscription

        try:
            while not self._parar.wait(self.intervalo):
                try:
                    (MeetingTranscription.objects
                     .filter(pk=self.transcricao_id, processando_por=self.dono)
                     .update(batimento_em=timezone.now()))
                except Exception as exc:                            # noqa: BLE001
                    logger.warning('Batimento da transcrição %s falhou: %s', self.transcricao_id, exc)
        finally:
            connection.close()

    def __enter__(self):
        self._thread = threading.Thread(target=self._bater, daemon=True,
                                        name=f'transcricao-batimento-{self.transcricao_id}')
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._parar.set()
        return False


def registrar_falha(transcricao_id, dono, exc):
    """Falha passageira vira nova tentativa agendada; permanente vira erro."""
    from .models import MeetingTranscription
    from .views import _friendly_openai_error

    t = MeetingTranscription.objects.filter(pk=transcricao_id).first()
    if t is None or t.status != 'processing':
        return None
    if dono and t.processando_por and t.processando_por != dono:
        return None

    agora = timezone.now()
    tentativas = (t.tentativas or 0) + 1
    mensagem = _friendly_openai_error(exc)
    campos = {'tentativas': tentativas, 'processando_por': '', 'batimento_em': None, 'updated_at': agora}
    if isinstance(exc, ErroPermanente) or tentativas >= MAX_TENTATIVAS:
        campos.update(status='error', etapa='', proxima_tentativa_em=None,
                      error_message=(mensagem if isinstance(exc, ErroPermanente)
                                     else f'{mensagem} (o portal tentou {tentativas} vezes)'))
        texto = (t.raw_transcription or '').strip()
        if texto and not (t.formatted_transcription or '').strip():
            # Como antes: o texto bruto aparece na tela mesmo sem a análise.
            campos['formatted_transcription'] = texto
    else:
        proxima = agora + backoff(tentativas)
        hora = timezone.localtime(proxima).strftime('%H:%M')
        campos.update(proxima_tentativa_em=proxima,
                      error_message=(f'A tentativa {tentativas} não deu certo: {mensagem} '
                                     f'O portal tenta de novo sozinho às {hora}.'))
    MeetingTranscription.objects.filter(pk=transcricao_id).update(**campos)
    return campos.get('status', 'processing')


def executar_job(transcricao_id, dono, modo='upload', opcoes=None):
    """Roda o pipeline de uma transcrição já reivindicada. True se concluiu."""
    from . import views as pipeline

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    try:
        with Batimento(transcricao_id, dono):
            if not api_key:
                if modo in ('reprocess', 'retomar'):
                    pipeline.concluir_sem_ia(transcricao_id, dono)
                    return True
                raise ErroPermanente('Chave da API OpenAI não configurada (OPENAI_API_KEY).')
            import openai

            cliente = openai.OpenAI(api_key=api_key, timeout=900, max_retries=2)
            pipeline.processar_transcricao(transcricao_id, cliente, dono=dono, modo=modo, opcoes=opcoes or {})
        logger.info('Transcrição %s concluída (%s).', transcricao_id, modo)
        return True
    except PerdeuAReivindicacao:
        logger.info('Transcrição %s: outro processo assumiu; este job parou.', transcricao_id)
        return False
    except Exception as exc:                                        # noqa: BLE001
        logger.warning('Transcrição %s falhou (%s): %s', transcricao_id, modo, exc,
                       exc_info=not isinstance(exc, ErroPermanente))
        registrar_falha(transcricao_id, dono, exc)
        return False


def _jobs_permitidos(sincrono):
    if not em_processo_de_teste():
        return True
    return bool(sincrono and PERMITIR_JOBS_NOS_TESTES)


def iniciar_job(transcricao_id, modo='upload', opcoes=None, sincrono=False):
    """Reivindica e processa: numa thread (servidor) ou na hora (comando, testes).

    Devolve False quando não iniciou — outro job está nela, a espera de nova
    tentativa não venceu, ou este processo já está com o máximo de jobs. Nada
    se perde nesses casos: a transcrição segue em processamento e o varredor de
    qualquer worker a pega.
    """
    if not _jobs_permitidos(sincrono):
        return False
    with _trava_jobs:
        if transcricao_id in _jobs_ativos:
            return False
        if not sincrono and len(_jobs_ativos) >= MAX_JOBS_POR_PROCESSO:
            return False
        _jobs_ativos.add(transcricao_id)

    try:
        dono = novo_dono()
        reivindicou = reivindicar(transcricao_id, dono)
    except Exception:
        with _trava_jobs:
            _jobs_ativos.discard(transcricao_id)
        raise
    if not reivindicou:
        with _trava_jobs:
            _jobs_ativos.discard(transcricao_id)
        return False

    if sincrono:
        try:
            executar_job(transcricao_id, dono, modo, opcoes)
        finally:
            with _trava_jobs:
                _jobs_ativos.discard(transcricao_id)
        return True

    threading.Thread(target=_rodar_em_thread, args=(transcricao_id, dono, modo, opcoes or {}),
                     daemon=True, name=f'transcricao-{modo}-{transcricao_id}').start()
    return True


def _rodar_em_thread(transcricao_id, dono, modo, opcoes):
    close_old_connections()
    try:
        executar_job(transcricao_id, dono, modo, opcoes)
    finally:
        with _trava_jobs:
            _jobs_ativos.discard(transcricao_id)
        connection.close()


def retomar_paradas(limite=RETOMADAS_POR_VARREDURA, excluir=None, sincrono=False, somente_ids=None):
    """Inicia jobs para transcrições em processamento sem job vivo e fora de espera."""
    from .models import MeetingTranscription

    agora = timezone.now()
    parado = agora - timedelta(seconds=PARADO_APOS_SEGUNDOS)
    qs = (MeetingTranscription.objects.filter(status='processing')
          .filter(Q(batimento_em__isnull=True) | Q(batimento_em__lt=parado))
          .filter(Q(proxima_tentativa_em__isnull=True) | Q(proxima_tentativa_em__lte=agora)))
    if excluir:
        qs = qs.exclude(pk=excluir)
    if somente_ids is not None:
        qs = qs.filter(pk__in=list(somente_ids))
    iniciadas = 0
    for pk in qs.order_by('updated_at', 'created_at').values_list('pk', flat=True)[:max(1, limite) * 3]:
        if iniciadas >= limite:
            break
        if iniciar_job(pk, modo='retomar', sincrono=sincrono):
            iniciadas += 1
    return iniciadas


def fechar_gravacoes_orfas(limite=10, somente_ids=None):
    """Gravação sem parte nova há 20 min: o navegador sumiu. Processa o que chegou."""
    from .models import MeetingTranscription

    agora = timezone.now()
    corte = agora - timedelta(minutes=GRAVACAO_ORFA_MINUTOS)
    qs = MeetingTranscription.objects.filter(status='recording', partes_recebidas__gt=0,
                                             ultima_parte_em__lt=corte)
    if somente_ids is not None:
        qs = qs.filter(pk__in=list(somente_ids))
    fechadas = 0
    for pk in qs.order_by('ultima_parte_em').values_list('pk', flat=True)[:limite]:
        fechadas += (MeetingTranscription.objects
                     .filter(pk=pk, status='recording', ultima_parte_em__lt=corte)
                     .update(status='processing', etapa='montagem', finalizada_automaticamente=True,
                             tentativas=0, proxima_tentativa_em=None, batimento_em=None,
                             processando_por='', error_message='', updated_at=agora))
    if fechadas:
        logger.info('%s gravação(ões) sem navegador foram fechadas para processar.', fechadas)
    return fechadas


def apagar_gravacoes_vazias(somente_ids=None):
    """Sessão aberta há 2 h sem nenhum áudio: ninguém gravou nada."""
    from .models import MeetingTranscription

    corte = timezone.now() - timedelta(hours=GRAVACAO_VAZIA_HORAS)
    qs = MeetingTranscription.objects.filter(status='recording', partes_recebidas=0, created_at__lt=corte)
    if somente_ids is not None:
        qs = qs.filter(pk__in=list(somente_ids))
    ids = list(qs.values_list('pk', flat=True)[:50])
    if ids:
        MeetingTranscription.objects.filter(pk__in=ids, status='recording', partes_recebidas=0).delete()
    return len(ids)


def limpar_partes_antigas(limite=20, somente_ids=None):
    """Apaga as partes de gravações concluídas há 7 dias — o áudio montado fica."""
    from .models import MeetingTranscription
    from .views import _apagar_partes

    corte = timezone.now() - timedelta(days=PARTES_RETENCAO_DIAS)
    qs = (MeetingTranscription.objects.filter(status='completed', updated_at__lt=corte)
          .exclude(upload_id='').exclude(audio_file='').exclude(audio_file__isnull=True))
    if somente_ids is not None:
        qs = qs.filter(pk__in=list(somente_ids))
    limpas = 0
    for t in qs.order_by('updated_at')[:limite * 5]:
        if limpas >= limite:
            break
        progresso = dict(t.progresso or {})
        if progresso.get('partes_apagadas'):
            continue
        try:
            if not t.audio_file.storage.exists(t.audio_file.name):
                continue    # sem o áudio montado, as partes são a única cópia
            _apagar_partes(t.upload_id)
        except Exception as exc:                                    # noqa: BLE001
            logger.warning('Partes da transcrição %s não foram apagadas: %s', t.pk, exc)
            continue
        progresso['partes_apagadas'] = True
        MeetingTranscription.objects.filter(pk=t.pk).update(progresso=progresso)
        limpas += 1
    return limpas


def varrer(sincrono=False, somente_ids=None):
    """Uma passada completa. Cada parte isolada: uma falha não impede as outras."""
    resumo = {'orfas_finalizadas': 0, 'vazias_apagadas': 0, 'retomadas': 0, 'partes_limpas': 0}
    for chave, funcao, kwargs in (
        ('orfas_finalizadas', fechar_gravacoes_orfas, {}),
        ('vazias_apagadas', apagar_gravacoes_vazias, {}),
        ('retomadas', retomar_paradas, {'sincrono': sincrono}),
        ('partes_limpas', limpar_partes_antigas, {}),
    ):
        try:
            resumo[chave] = funcao(somente_ids=somente_ids, **kwargs)
        except Exception:                                           # noqa: BLE001
            logger.exception('Varredura de transcrições: "%s" falhou', chave)
    return resumo


def garantir_varredura():
    """Liga a varredura em segundo plano deste processo, se ainda não está viva."""
    global _thread_varredura
    if not deve_rodar_varredura():
        return False
    with _trava_varredura:
        if _thread_varredura is not None and _thread_varredura.is_alive():
            return False
        _thread_varredura = threading.Thread(target=_loop_varredura, daemon=True,
                                             name='transcricao-varredura')
        _thread_varredura.start()
    logger.info('Varredura de transcrições ligada no processo %s.', os.getpid())
    return True


def _loop_varredura():
    # Espalha os workers: todos subindo juntos no deploy não varrem no mesmo segundo.
    time.sleep(random.uniform(5, 20))
    while True:
        try:
            resumo = varrer()
            if any(resumo.values()):
                logger.info('Varredura de transcrições: %s', resumo)
        except Exception:                                           # noqa: BLE001
            logger.exception('Varredura de transcrições falhou')
        finally:
            connection.close()
        time.sleep(INTERVALO_VARREDURA + random.uniform(0, 15))
