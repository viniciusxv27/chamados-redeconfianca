"""Cliente HTTP da API do Tangerino (Sólides Ponto).

São dois serviços diferentes, com hosts distintos e o mesmo token:

* ``employer``  -> cadastros, ajustes/afastamentos (inclusive FÉRIAS)
* ``punch``     -> marcações de ponto

Regra de ouro deste módulo: **o portal nunca pode quebrar porque o Tangerino
caiu**. Toda função de leitura devolve um valor neutro em caso de falha e
registra no log. Só as ações de escrita (bater ponto) propagam o erro, porque
aí o usuário precisa saber que não foi registrado.
"""
import logging
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

EMPLOYER_BASE = 'https://employer.tangerino.com.br'
PUNCH_BASE = 'https://api.tangerino.com.br/api/punch'

TIMEOUT = (5, 25)          # (conexão, leitura) em segundos
MOTIVO_FERIAS_ID = 1       # "FÉRIAS" em /adjustment-reason/find-all


class TangerinoError(Exception):
    """Falha ao falar com a API do Tangerino."""


class TangerinoDesligado(TangerinoError):
    """Integração desativada ou sem token configurado."""


# ─── Infraestrutura ──────────────────────────────────────────────────────────

def integracao_ativa():
    return bool(getattr(settings, 'TANGERINO_ENABLED', False)
                and getattr(settings, 'TANGERINO_TOKEN', ''))


def _headers():
    token = getattr(settings, 'TANGERINO_TOKEN', '')
    if not token:
        raise TangerinoDesligado('TANGERINO_TOKEN não configurado.')
    return {'Authorization': token, 'Content-Type': 'application/json'}


def _request(metodo, base, caminho, **kwargs):
    if not integracao_ativa():
        raise TangerinoDesligado('Integração com o Tangerino está desligada.')
    url = f"{base}{caminho}"
    try:
        resp = requests.request(metodo, url, headers=_headers(), timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise TangerinoError(f'Não foi possível falar com o Tangerino: {exc}') from exc

    if resp.status_code >= 400:
        raise TangerinoError(f'Tangerino respondeu {resp.status_code} em {caminho}: '
                             f'{resp.text[:300]}')
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError as exc:
        raise TangerinoError(f'Resposta inválida do Tangerino em {caminho}.') from exc


def _get(base, caminho, params=None):
    return _request('GET', base, caminho, params=params or {})


def _post(base, caminho, corpo):
    return _request('POST', base, caminho, json=corpo)


def _paginar(base, caminho, params=None, tamanho=200, limite_paginas=40):
    """Percorre um endpoint paginado (padrão Spring: content/totalPages)."""
    params = dict(params or {})
    itens, pagina = [], 0
    while pagina < limite_paginas:
        params.update({'page': pagina, 'size': tamanho})
        dados = _get(base, caminho, params) or {}
        itens.extend(dados.get('content') or [])
        if dados.get('last') is True or pagina + 1 >= (dados.get('totalPages') or 1):
            break
        pagina += 1
    return itens


# ─── Conversão de datas ──────────────────────────────────────────────────────
# A API troca datas em milissegundos. Os horários são do fuso da empresa, que é
# o mesmo TIME_ZONE do Django — por isso a conversão passa sempre por
# ``timezone.localtime`` em vez de usar UTC direto.

def para_millis(quando):
    """date/datetime -> milissegundos. `date` vira o início do dia local."""
    if isinstance(quando, datetime):
        dt = quando if timezone.is_aware(quando) else timezone.make_aware(quando)
    else:
        dt = timezone.make_aware(datetime.combine(quando, time.min))
    return int(dt.timestamp() * 1000)


def fim_do_dia_millis(dia):
    return int(timezone.make_aware(datetime.combine(dia, time.max)).timestamp() * 1000)


def de_millis(valor):
    """Milissegundos -> datetime no fuso local. None/0 devolve None."""
    if not valor:
        return None
    return timezone.localtime(datetime.fromtimestamp(valor / 1000, tz=dt_timezone.utc))


# ─── Funcionários ────────────────────────────────────────────────────────────

def listar_funcionarios(usar_cache=True):
    """Todos os funcionários cadastrados no Tangerino."""
    chave = 'tangerino:funcionarios'
    if usar_cache:
        em_cache = cache.get(chave)
        if em_cache is not None:
            return em_cache
    itens = _paginar(EMPLOYER_BASE, '/employee/find-all')
    cache.set(chave, itens, 60 * 30)
    return itens


def funcionario(employee_id):
    return _get(EMPLOYER_BASE, '/employee/find', {'id': employee_id})


# ─── Marcações de ponto ──────────────────────────────────────────────────────

def listar_marcacoes(inicio, fim, employee_id=None, usar_cache=True, ttl=60):
    """Marcações num intervalo de dias.

    Cada item é um PAR entrada/saída (``dateIn``/``dateOut``); ``dateOut`` vazio
    significa que a pessoa entrou e ainda não saiu. Sem ``employee_id`` traz a
    empresa inteira, que é como as telas de gestor carregam tudo de uma vez.
    """
    chave = f"tangerino:marcacoes:{employee_id or 'todos'}:{inicio}:{fim}"
    if usar_cache:
        em_cache = cache.get(chave)
        if em_cache is not None:
            return em_cache

    params = {'startDateInMillis': para_millis(inicio), 'endDateInMillis': fim_do_dia_millis(fim)}
    if employee_id:
        params['employeeId'] = employee_id
    itens = _paginar(PUNCH_BASE, '/', params)
    cache.set(chave, itens, ttl)
    return itens


def listar_saldo_horas(inicio, fim, employee_id=None, tentativas=3):
    """Saldo de banco de horas de cada funcionário no período.

    Devolve o cálculo do próprio Tangerino (``hoursBalanceInMinutes``).

    A retentativa não é decorativa: este endpoint às vezes responde com um XML
    de erro do storage em vez do JSON — foi visto voltando vazio numa chamada e
    completo na seguinte, com os mesmos parâmetros. Sem isso, uma sincronização
    gravaria "sem saldo" para a empresa inteira por causa de um soluço.
    """
    params = {'startDate': para_millis(inicio), 'endDate': fim_do_dia_millis(fim)}
    if employee_id:
        params['employeeId'] = employee_id

    ultimo_erro = None
    for tentativa in range(tentativas):
        try:
            dados = _get(PUNCH_BASE, '/hoursBalance', params)
            if isinstance(dados, list):
                return dados
            ultimo_erro = TangerinoError('Resposta inesperada no saldo de horas.')
        except TangerinoError as exc:
            ultimo_erro = exc
        logger.warning('Saldo de horas falhou (tentativa %d/%d): %s',
                       tentativa + 1, tentativas, ultimo_erro)
    raise ultimo_erro


def justificativas_edicao():
    """Motivos válidos para uma marcação retroativa."""
    chave = 'tangerino:justificativas'
    em_cache = cache.get(chave)
    if em_cache is not None:
        return em_cache
    dados = _get(PUNCH_BASE, '/manual-editing-justification-punch/', {'page': 0, 'size': 100})
    itens = (dados or {}).get('content') if isinstance(dados, dict) else (dados or [])
    itens = itens or []
    cache.set(chave, itens, 60 * 60)
    return itens


def limpar_base64(valor):
    """Tira o prefixo "data:image/jpeg;base64," que o canvas do navegador manda.

    A API espera o conteúdo puro em base64; com o prefixo ela rejeita a foto.
    """
    if not valor:
        return ''
    texto = str(valor).strip()
    if texto.startswith('data:'):
        _, _, texto = texto.partition(',')
    return texto.strip()


def para_wire(quando):
    """Formata a data do jeito que a API do Tangerino espera: **em UTC**.

    Descoberto na marra, comparando o que foi enviado com o que ficou gravado:
    um clique às 15:54:50 (São Paulo) enviado como "2026-08-17T15:54:50"
    virou 12:54:50 lá dentro. Ou seja, eles leem a string sem fuso como UTC e
    exibem convertido para o fuso da empresa — o que subtraía 3 horas de toda
    marcação. Mandando o horário já em UTC, grava certo.
    """
    if timezone.is_naive(quando):
        quando = timezone.make_aware(quando)
    return quando.astimezone(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


def enviar_foto(foto_base64):
    """Sobe a foto e devolve a URL dela no storage do Tangerino.

    O ponto não guarda os bytes da imagem: guarda uma referência
    (``photoIn.photoURL``). Mandar só ``photoContent`` passava na validação de
    "tem foto?", mas a imagem não ficava anexada à marcação — era o que fazia a
    foto sumir. O caminho certo é subir aqui e depois registrar o ponto com o
    ``photoURL`` devolvido.

    O corpo vai como text/plain puro, não JSON: é o que o endpoint aceita
    (com JSON ele responde 406).
    """
    conteudo = limpar_base64(foto_base64)
    if not conteudo:
        return ''
    if not integracao_ativa():
        raise TangerinoDesligado('Integração com o Tangerino está desligada.')

    token = getattr(settings, 'TANGERINO_TOKEN', '')
    try:
        resp = requests.post(
            f'{PUNCH_BASE}/upload-pic-files',
            headers={'Authorization': token, 'Content-Type': 'text/plain;charset=UTF-8'},
            data=conteudo.encode('utf-8'), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise TangerinoError(f'Falha ao enviar a foto: {exc}') from exc

    if resp.status_code >= 400:
        raise TangerinoError(f'O Tangerino recusou a foto ({resp.status_code}): '
                             f'{resp.text[:200]}')
    url = (resp.text or '').strip()
    if not url.startswith('http'):
        raise TangerinoError('O Tangerino não devolveu a URL da foto.')
    return url


def registrar_ponto(employee_id, quando=None, latitude=None, longitude=None,
                    endereco='', origem='PORTAL RC', foto_base64=''):
    """Registra uma marcação AGORA (ou no horário informado).

    A foto não é enfeite: esta empresa tem o Tangerino configurado para recusar
    marcação pela web sem ela — "O Colaborador não está autorizado a registrar
    Ponto pela Web sem envio de Foto". Sem `photoContent` a marcação falha.

    Escrita: propaga erro de propósito. A API responde 200 mesmo quando recusa,
    sinalizando em ``success`` — por isso o resultado é inspecionado aqui.
    """
    quando = quando or timezone.localtime()
    corpo = {
        'employeeId': employee_id,
        'date': para_wire(quando),
        'origin': origem,
        'timezoneId': str(timezone.get_current_timezone()),
        'validTimezone': True,
        'platform': 'WEB',
    }

    # Foto: sobe primeiro e referencia pela URL. O photoContent segue junto
    # porque é o que satisfaz a validação de "marcação web precisa de foto";
    # a URL é o que faz a imagem realmente ficar anexada ao registro.
    foto_url = ''
    foto = limpar_base64(foto_base64)
    if foto:
        corpo['photoContent'] = foto
        try:
            foto_url = enviar_foto(foto)
            corpo['photoURL'] = foto_url
        except TangerinoError as exc:
            # Não trava a marcação por causa do storage de imagem: o ponto é o
            # que não pode se perder. Quem chamou recebe o aviso e decide.
            logger.warning('Foto não subiu, seguindo só com photoContent: %s', exc)

    if latitude is not None and longitude is not None:
        corpo.update({'latitude': latitude, 'longitude': longitude, 'gpsDisable': False,
                      'mockLocationEnabled': False})
    else:
        corpo['gpsDisable'] = True
    if endereco:
        corpo['address'] = endereco[:250]

    resposta = _post(PUNCH_BASE, '/register/web/1.1', corpo) or {}
    if resposta.get('success') is False:
        raise TangerinoError(resposta.get('message') or 'O Tangerino recusou a marcação.')
    resposta['_foto_url'] = foto_url
    resposta['_enviou_local'] = latitude is not None and longitude is not None
    return resposta


def registrar_ponto_atrasado(employee_id, quando, justificativa_id, observacao='',
                             foto_base64=''):
    """Marcação retroativa (ponto esquecido), que exige justificativa."""
    corpo = {
        'employeeId': employee_id,
        'date': para_wire(quando),          # em UTC, mesmo motivo do ponto normal
        'manualEditingJustificationId': justificativa_id,
        'latePunch': True,
        'origin': 'PORTAL RC',
        'platform': 'WEB',
        'gpsDisable': True,
        'timezoneId': str(timezone.get_current_timezone()),
    }
    foto = limpar_base64(foto_base64)
    if foto:
        corpo['photoContent'] = foto
        try:
            corpo['photoURL'] = enviar_foto(foto)
        except TangerinoError as exc:
            logger.warning('Foto da marcação retroativa não subiu: %s', exc)
    if observacao:
        corpo['observationEmployee'] = observacao[:250]
    resposta = _post(PUNCH_BASE, '/register/late/1.1', corpo) or {}
    if resposta.get('success') is False:
        raise TangerinoError(resposta.get('message') or 'O Tangerino recusou a marcação.')
    return resposta


# ─── Ajustes / Férias ────────────────────────────────────────────────────────

def listar_ferias(usar_cache=True, ttl=60 * 10):
    """Todos os lançamentos de FÉRIAS da empresa.

    O endpoint não filtra por data — devolve a lista inteira, que é pequena
    (dezenas de registros). O recorte por período é feito em memória.
    """
    chave = 'tangerino:ferias'
    if usar_cache:
        em_cache = cache.get(chave)
        if em_cache is not None:
            return em_cache
    itens = _paginar(EMPLOYER_BASE, '/adjustment/find-all',
                     {'adjustmentReasonId': MOTIVO_FERIAS_ID, 'ignoreExcluded': True})
    cache.set(chave, itens, ttl)
    return itens


def jornada(schedule_id, usar_cache=True, ttl=60 * 60 * 6):
    """A escala contratada de uma pessoa, com os turnos de cada dia da semana.

    Este endpoint não aparece junto dos outros ``find-all``: ele responde por
    ``/work-schedule/{id}``, e o id vem em ``currentWorkSchedule`` do
    funcionário. É o que permite dizer quantas horas alguém **deveria** ter
    trabalhado — sem ele, só dava para mostrar o que a pessoa fez.

    Os horários vêm em milissegundos contados do início do dia
    (``54000000`` = 15:00) e o campo ``day`` segue o padrão do Java:
    1 = domingo … 7 = sábado. Dia sem linha na grade é folga.
    """
    chave = f'tangerino:jornada:{schedule_id}'
    if usar_cache:
        em_cache = cache.get(chave)
        if em_cache is not None:
            return em_cache
    dados = _get(EMPLOYER_BASE, f'/work-schedule/{schedule_id}')
    cache.set(chave, dados, ttl)
    return dados


def listar_ajustes(motivo_id, usar_cache=True, ttl=60 * 10):
    """Lançamentos de um motivo de ajuste (feriado, atestado, folga…).

    Mesmo endpoint das férias, trocando o motivo. Serve para descontar do
    previsto os dias em que a pessoa não devia mesmo estar trabalhando.
    """
    chave = f'tangerino:ajustes:{motivo_id}'
    if usar_cache:
        em_cache = cache.get(chave)
        if em_cache is not None:
            return em_cache
    itens = _paginar(EMPLOYER_BASE, '/adjustment/find-all',
                     {'adjustmentReasonId': motivo_id, 'ignoreExcluded': True})
    cache.set(chave, itens, ttl)
    return itens


def motivos_de_ajuste():
    chave = 'tangerino:motivos'
    em_cache = cache.get(chave)
    if em_cache is not None:
        return em_cache
    itens = _paginar(EMPLOYER_BASE, '/adjustment-reason/find-all')
    cache.set(chave, itens, 60 * 60 * 6)
    return itens


def invalidar_cache_ferias():
    cache.delete('tangerino:ferias')


def invalidar_cache_marcacoes(employee_id, dia=None):
    dia = dia or timezone.localdate()
    for alvo in (employee_id, 'todos'):
        cache.delete(f"tangerino:marcacoes:{alvo}:{dia}:{dia}")
        # A visão semanal do widget também precisa cair.
        inicio_semana = dia - timedelta(days=dia.weekday())
        cache.delete(f"tangerino:marcacoes:{alvo}:{inicio_semana}:{dia}")


# ─── Diagnóstico ─────────────────────────────────────────────────────────────

def testar_conexao():
    """Usado pela tela de administração para checar o token."""
    try:
        dados = _get(EMPLOYER_BASE, '/employee/find-all', {'page': 0, 'size': 1}) or {}
        return True, f"Conectado. {dados.get('totalElements', 0)} funcionários no Tangerino."
    except TangerinoError as exc:
        return False, str(exc)
