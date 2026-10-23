"""Ajuste de ponto pelo portal (23/10/2026).

O colaborador pede o ajuste em /ponto (alterar o horário de uma batida, incluir uma
batida esquecida ou remover um par entrada/saída). O pedido vai para o setor que o
SUPERADMIN escolheu em /ponto/configuracao/; quem tem esse setor como setor principal
aprova (e o portal grava na Sólides), recusa, ou edita direto o ponto de qualquer
pessoa — inclusive o próprio.

Nasce desligado (``ConfiguracaoTangerino.ajustes_ativo``): mexe no ponto oficial.
"""
import json
import logging

from django.db import transaction
from django.utils import timezone

from .client import (TangerinoError, achar_marcacao, alterar_marcacao, excluir_marcacao,
                     invalidar_cache_marcacoes, registrar_ponto_atrasado)
from .models import ConfiguracaoTangerino, SolicitacaoAjustePonto

logger = logging.getLogger(__name__)
Sol = SolicitacaoAjustePonto


class RegraAjuste(Exception):
    """Mensagem para mostrar na tela."""


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def ligado(config=None):
    config = config or ConfiguracaoTangerino.get()
    return bool(config.ajustes_ativo and config.ajustes_setor_id)


def e_responsavel(user, config=None):
    """Quem aprova e edita o ponto de qualquer pessoa: o setor escolhido (setor principal) e o SUPERADMIN."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    config = config or ConfiguracaoTangerino.get()
    if not ligado(config):
        return False
    return e_superadmin(user) or (bool(user.sector_id) and user.sector_id == config.ajustes_setor_id)


def responsaveis(config=None):
    from django.contrib.auth import get_user_model
    config = config or ConfiguracaoTangerino.get()
    if not config.ajustes_setor_id:
        return []
    return list(get_user_model().objects.filter(is_active=True, sector_id=config.ajustes_setor_id))


def _notificar(usuarios, titulo, mensagem, url):
    try:
        from core.models import Notification
        Notification.objects.bulk_create([Notification(user=u, title=titulo[:200], message=mensagem,
                                                       notification_type='SYSTEM', related_url=url)
                                          for u in usuarios if u])
    except Exception:  # noqa: BLE001 — aviso nunca derruba o pedido
        logger.warning('Ajuste de ponto: aviso não enviado', exc_info=True)


def _nome(u):
    return (u.get_full_name() or u.email) if u else 'sistema'


def _local(dt):
    return timezone.localtime(dt) if dt else None


def criar(usuario, solicitante, tipo, dia, antigo=None, novo=None, motivo='', justificativa_id=None,
          justificativa_texto='', direto=False):
    """Cria o pedido. ``direto`` (setor responsável editando): aplica na Sólides na hora."""
    config = ConfiguracaoTangerino.get()
    if not ligado(config):
        raise RegraAjuste('O ajuste de ponto pelo portal está desligado.')
    if direto and not e_responsavel(solicitante, config):
        raise RegraAjuste('Só o setor responsável edita o ponto direto.')
    if not direto and usuario.pk != solicitante.pk:
        raise RegraAjuste('Você só pede ajuste do seu próprio ponto.')
    employee_id = getattr(usuario, 'tangerino_employee_id', None)
    if not employee_id:
        raise RegraAjuste(f'{_nome(usuario)} não está vinculado a um funcionário da Sólides.')
    if tipo not in dict(Sol.TIPOS):
        raise RegraAjuste('Escolha o tipo de ajuste.')
    hoje, agora = timezone.localdate(), timezone.localtime()
    if not dia or dia > hoje:
        raise RegraAjuste('Escolha um dia que já passou (ou hoje).')
    antigo, novo = _local(antigo), _local(novo)
    if tipo in (Sol.ALTERAR, Sol.EXCLUIR) and not antigo:
        raise RegraAjuste('Escolha a batida que vai mudar.')
    if tipo in (Sol.ALTERAR, Sol.INCLUIR):
        if not novo:
            raise RegraAjuste('Informe o horário certo.')
        if novo > agora:
            raise RegraAjuste('O horário certo não pode estar no futuro.')
        if (novo.date() - dia).days not in (0, 1):
            raise RegraAjuste('O horário certo precisa ser do mesmo dia (ou da madrugada seguinte).')
    if tipo == Sol.ALTERAR and antigo.replace(second=0, microsecond=0) == novo.replace(second=0, microsecond=0):
        raise RegraAjuste('O horário novo é igual ao atual.')
    if tipo == Sol.INCLUIR and not justificativa_id:
        raise RegraAjuste('Escolha a justificativa (a Sólides exige para incluir batida).')
    motivo = str(motivo or '').strip()
    if len(motivo) < 5:
        raise RegraAjuste('Conte o motivo do ajuste.')
    if antigo and Sol.objects.filter(employee_id=employee_id, status=Sol.PENDENTE, horario_antigo=antigo).exists():
        raise RegraAjuste('Já existe um pedido aguardando para esta batida.')
    sol = Sol.objects.create(usuario=usuario, employee_id=employee_id, solicitante=solicitante, direto=direto,
                             tipo=tipo, dia=dia, horario_antigo=antigo, horario_novo=novo, motivo=motivo[:1000],
                             justificativa_id=justificativa_id or None,
                             justificativa_texto=str(justificativa_texto or '')[:200])
    if direto:
        sol.decidido_por, sol.decidido_em = solicitante, timezone.now()
        sol.save(update_fields=['decidido_por', 'decidido_em'])
        aplicar(sol, solicitante)
    else:
        _notificar(responsaveis(config), 'Pedido de ajuste de ponto',
                   f'{_nome(usuario)} pediu: {sol.get_tipo_display().lower()} em {dia:%d/%m/%Y} ({sol.resumo}).',
                   '/ponto/ajustes/')
    return sol


def aplicar(sol, por):
    """Grava na Sólides. Erro da Sólides não some: o pedido fica ERRO, com a mensagem, para tentar de novo."""
    observacao = f'{sol.motivo} — ajuste pelo portal, {"editado" if sol.direto else "aprovado"} por {_nome(por)}'
    try:
        if sol.tipo == Sol.INCLUIR:
            resposta = registrar_ponto_atrasado(sol.employee_id, sol.horario_novo, sol.justificativa_id,
                                                observacao=observacao, aprovada=True)
        else:
            punch_id, _batida = achar_marcacao(sol.employee_id, sol.horario_antigo)
            sol.punch_id = punch_id
            if sol.tipo == Sol.ALTERAR:
                resposta = alterar_marcacao(sol.employee_id, punch_id, sol.horario_antigo, sol.horario_novo,
                                            observacao=observacao)
            else:
                resposta = excluir_marcacao(sol.employee_id, punch_id)
        sol.status, sol.aplicada_em = Sol.APLICADA, timezone.now()
        sol.retorno_solides = json.dumps(resposta, ensure_ascii=False, default=str)[:4000]
    except Exception as exc:  # noqa: BLE001 — TangerinoError, desligado, rede: tudo vira ERRO visível
        sol.status = Sol.ERRO
        sol.retorno_solides = f'{type(exc).__name__}: {exc}'[:4000]
        logger.warning('Ajuste de ponto %s não aplicado: %s', sol.pk, exc)
    sol.save(update_fields=['status', 'aplicada_em', 'retorno_solides', 'punch_id'])
    if sol.status == Sol.APLICADA:
        invalidar_cache_marcacoes(sol.employee_id)
        _ressincronizar(sol)
    if sol.solicitante_id and sol.solicitante_id != getattr(por, 'pk', None):
        _notificar([sol.solicitante], 'Ajuste de ponto aplicado' if sol.status == Sol.APLICADA
                   else 'Ajuste de ponto: a Sólides recusou',
                   f'{sol.get_tipo_display()} em {sol.dia:%d/%m/%Y} ({sol.resumo}).'
                   + ('' if sol.status == Sol.APLICADA else f' Motivo: {sol.retorno_solides[:200]}'), '/ponto/')
    return sol


def _ressincronizar(sol):
    """O espelho do portal (MarcacaoPonto) acompanha a Sólides; falhar aqui não desfaz o ajuste."""
    try:
        from .sync import sincronizar_periodo
        sincronizar_periodo(sol.dia, sol.dia, employee_ids=[sol.employee_id])
    except Exception:  # noqa: BLE001
        logger.warning('Ajuste de ponto %s: espelho não ressincronizado agora', sol.pk, exc_info=True)


@transaction.atomic
def aprovar(sol, por):
    sol = Sol.objects.select_for_update().get(pk=sol.pk)
    if not e_responsavel(por):
        raise RegraAjuste('Só o setor responsável aprova ajuste de ponto.')
    if sol.status not in (Sol.PENDENTE, Sol.ERRO):
        raise RegraAjuste('Este pedido já foi decidido.')
    sol.decidido_por, sol.decidido_em = por, timezone.now()
    sol.save(update_fields=['decidido_por', 'decidido_em'])
    return aplicar(sol, por)


@transaction.atomic
def recusar(sol, por, resposta):
    sol = Sol.objects.select_for_update().get(pk=sol.pk)
    if not e_responsavel(por):
        raise RegraAjuste('Só o setor responsável recusa ajuste de ponto.')
    if sol.status not in (Sol.PENDENTE, Sol.ERRO):
        raise RegraAjuste('Este pedido já foi decidido.')
    resposta = str(resposta or '').strip()
    if not resposta:
        raise RegraAjuste('Diga o motivo da recusa.')
    sol.status, sol.resposta = Sol.RECUSADA, resposta[:1000]
    sol.decidido_por, sol.decidido_em = por, timezone.now()
    sol.save(update_fields=['status', 'resposta', 'decidido_por', 'decidido_em'])
    _notificar([sol.solicitante], 'Ajuste de ponto recusado',
               f'{sol.get_tipo_display()} em {sol.dia:%d/%m/%Y} ({sol.resumo}): {resposta[:300]}', '/ponto/')
    return sol


@transaction.atomic
def cancelar(sol, por):
    sol = Sol.objects.select_for_update().get(pk=sol.pk)
    if sol.solicitante_id != por.pk or sol.status != Sol.PENDENTE:
        raise RegraAjuste('Só dá para cancelar o próprio pedido que ainda aguarda.')
    sol.status = Sol.CANCELADA
    sol.save(update_fields=['status'])
    return sol
