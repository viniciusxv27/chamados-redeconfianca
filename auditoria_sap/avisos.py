"""Aviso aos gestores da Visão SAP quando alguém marca uma linha como resolvida.

Quem recebe é quem o SUPERADMIN escolheu em /sap/gestores/ (menos quem marcou).
O aviso leva direto à linha na auditoria, com o filtro em "todas" — senão ela não
apareceria, porque a lista abre só nas abertas.
"""
import logging
from urllib.parse import urlencode

from django.urls import reverse

from .permissions import gestores_escolhidos

logger = logging.getLogger(__name__)


def link_da_linha(linha):
    chave = next((v for v in (linha.id_venda, linha.documento_vivogo, linha.serial_vivogo, linha.num_fat_sap)
                  if (v or '').strip()), '')
    parametros = {'situacao': 'todas', 'presenca': 'todas'}
    if chave:
        parametros['q'] = chave
    if linha.pdv:
        parametros['loja'] = linha.pdv
    return f"{reverse('auditoria_sap:lista')}?{urlencode(parametros)}"


def avisar_resolvida(linha, quem):
    """Sino e push para os gestores escolhidos. Devolve quem foi avisado; falha não derruba a marcação."""
    destinos = [u for u in gestores_escolhidos() if u.pk != getattr(quem, 'pk', None)]
    if not destinos:
        return []
    nome = getattr(quem, 'full_name', '') or 'Alguém'
    venda = f'venda #{linha.id_venda}' if linha.id_venda else 'uma linha'
    mensagem = f'{nome} marcou {venda} ({(linha.pdv or "sem loja").title()}) como resolvida.'
    if linha.observacao:
        mensagem += f' Obs.: {linha.observacao[:200]}'
    try:
        from notifications.services import NotificationChannel, NotificationType, notification_service

        canais = [NotificationChannel.IN_APP, NotificationChannel.PUSH]
        if notification_service.onesignal_enabled:
            canais.append(NotificationChannel.ONESIGNAL)
        notification_service.send_notification(
            destinos, f'SAP: {linha.tipo_erro or "divergência"} resolvida', mensagem,
            notification_type=NotificationType.SYSTEM, channels=canais, action_url=link_da_linha(linha),
            icon='fas fa-file-invoice-dollar', extra_data={'linha_sap': linha.pk}, created_by=quem)
    except Exception as exc:                                    # noqa: BLE001 — a marcação já valeu
        logger.warning('Aviso de linha SAP resolvida (%s) não foi enviado: %s', linha.pk, exc)
        return []
    return destinos
