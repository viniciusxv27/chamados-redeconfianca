"""O que o Renova faz fora dele: abrir o chamado e registrar a chegada do aparelho."""
import logging

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from . import checklist
from .models import ConfiguracaoRenova, Renova

logger = logging.getLogger(__name__)


def _moeda(valor):
    if valor is None:
        return '—'
    texto = f'{valor:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    return f'R$ {texto}'


def _nome(user):
    return (getattr(user, 'full_name', '') or user.get_username()) if user else '—'


def _link(renova):
    return (getattr(settings, 'BASE_URL', '') or '').rstrip('/') + reverse('renova:detalhe', args=[renova.pk])


def descricao_do_chamado(renova):
    """O checklist inteiro em texto, para quem recebe o aparelho ler no chamado."""
    obrigatorios = renova.itens_obrigatorios_lista()
    faltando = [titulo for _, titulo, _, _, feito in obrigatorios if not feito]
    padrao = f'{renova.padrao} ({renova.get_padrao_display()})' if renova.padrao else '—'
    bateria = f'{renova.saude_bateria}%' if renova.saude_bateria is not None else '—'
    linhas = [
        f'Vini Renova {renova.codigo} — avaliação de aparelho usado',
        '',
        f'Aparelho: {renova.aparelho}' + (f' · cor {renova.cor}' if renova.cor else ''),
        f'IMEI 1: {renova.imei1}' + (f' · IMEI 2: {renova.imei2}' if renova.imei2 else ''),
        f'Loja (origem): {renova.loja.name if renova.loja else "—"}',
        f'Data da avaliação: {renova.data_avaliacao:%d/%m/%Y}',
        f'Padrão: {padrao} · valor estimado de troca: {_moeda(renova.valor_estimado)}',
        'Por que este padrão: ' + ('; '.join(m['texto'] for m in (renova.padrao_motivos or []))
                                   or 'nenhuma avaria sinalizada'),
        f'Saúde da bateria: {bateria}',
        f'Aprovação: {renova.get_aprovacao_display()}'
        + (f' por {_nome(renova.aprovacao_por)} em {timezone.localtime(renova.aprovacao_em):%d/%m/%Y %H:%M}'
           if renova.aprovacao_por_id and renova.aprovacao_em else '')
        + (f' — {renova.aprovacao_obs}' if renova.aprovacao_obs else ''),
        '',
        'Itens obrigatórios: ' + ('todos conferidos.' if not faltando else 'faltou ' + '; '.join(faltando) + '.'),
        '',
        'FUNCIONALIDADES',
    ]
    linhas += [f'• {titulo}: {rotulo}' for _, titulo, _, _, _, rotulo in renova.funcionalidades_lista()]
    linhas += ['', 'CONDIÇÃO ESTÉTICA']
    linhas += [f'• {titulo}: {rotulo}' for _, titulo, _, _, _, rotulo in renova.estetica_lista()]
    linhas += [
        '',
        f'Observações gerais: {renova.observacoes or "—"}',
        '',
        f'Responsável: {renova.vendedor_nome}' + (f' (matrícula {renova.matricula})' if renova.matricula else ''),
        '',
        f'Marcar a chegada do aparelho: {_link(renova)}',
    ]
    return '\n'.join(linhas)


def abrir_chamado(renova, autor):
    """Abre o chamado na categoria configurada e liga ao Renova.

    Devolve o Ticket, ou None quando não há categoria ativa configurada. O
    chamado passa pelo mesmo caminho de quem abre pela tela: avisos do setor e
    webhooks da categoria acontecem nos sinais do próprio app de chamados.
    """
    from tickets.models import Ticket, TicketLog

    categoria = ConfiguracaoRenova.get().categoria
    if categoria is None or not categoria.is_active:
        return None
    loja = renova.loja.name if renova.loja else ''
    titulo = f'Vini Renova {renova.codigo} — {renova.aparelho}' + (f' ({loja})' if loja else '')
    with transaction.atomic():
        ticket = Ticket.objects.create(
            title=titulo[:200],
            description=descricao_do_chamado(renova),
            sector=categoria.sector,
            category=categoria,
            created_by=autor,
            priority='MEDIA',
            store_location=loja[:200] or None,
            responsible_person=renova.vendedor_nome[:200] or None,
        )
        TicketLog.objects.create(ticket=ticket, user=autor, new_status='ABERTO',
                                 observation='Chamado criado pelo Vini Renova')
        renova.chamado = ticket
        renova.save(update_fields=['chamado', 'atualizado_em'])
    return ticket


def registrar_recebimento(renova, usuario, situacao, observacao=''):
    """Marca se o aparelho chegou e deixa isso no chamado.

    O comentário no chamado é o que avisa quem fez a avaliação (os avisos de
    comentário do app de chamados vão para quem abriu). Sem chamado, o aviso vai
    direto para o sino dessa pessoa.
    """
    if situacao not in dict(Renova.RECEBIMENTOS):
        raise ValueError(situacao)
    observacao = (observacao or '').strip()[:2000]
    renova.recebimento = situacao
    renova.recebimento_obs = observacao
    if situacao == Renova.PENDENTE:
        renova.recebido_por, renova.recebido_em = None, None
    else:
        renova.recebido_por, renova.recebido_em = usuario, timezone.now()
    renova.save(update_fields=['recebimento', 'recebimento_obs', 'recebido_por', 'recebido_em', 'atualizado_em'])

    quem = getattr(usuario, 'full_name', '') or usuario.get_username()
    texto = f'Vini Renova {renova.codigo}: aparelho marcado como "{renova.get_recebimento_display()}" por {quem}.'
    if observacao:
        texto += f' Observação: {observacao}'

    if renova.chamado_id:
        try:
            from tickets.models import TicketComment

            TicketComment.objects.create(ticket_id=renova.chamado_id, user=usuario, comment=texto,
                                         comment_type='FOLLOW_UP')
        except Exception as exc:                                # noqa: BLE001 — a marcação já valeu
            logger.warning('Comentário do Renova %s no chamado falhou: %s', renova.pk, exc)
    elif renova.criado_por_id and renova.criado_por_id != usuario.pk and situacao != Renova.PENDENTE:
        _avisar_no_sino(renova.criado_por, f'Renova {renova.codigo}: {renova.get_recebimento_display()}',
                        texto, reverse('renova:detalhe', args=[renova.pk]), usuario)
    return renova


def _avisar_no_sino(destinos, titulo, mensagem, url, autor):
    """Notificação só no sino do portal (sem push). Aceita uma pessoa ou uma lista."""
    destinos = [d for d in (destinos if isinstance(destinos, (list, tuple)) else [destinos]) if d]
    if not destinos:
        return
    try:
        from notifications.services import NotificationType, notification_service

        notification_service._send_in_app(destinos, titulo, mensagem, NotificationType.SYSTEM, url,
                                          'NORMAL', 'fas fa-mobile-screen-button', {}, autor)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Aviso do Renova não foi para o sino: %s', exc)


class DecisaoInvalida(Exception):
    """A aprovação pedida não vale (já decidida, ou reprovação sem motivo)."""


def gerentes_da_loja(renova):
    """Quem aprova esta troca: os membros ativos do grupo GERENTES com a loja dela como setor principal."""
    from .permissoes import grupo_gerentes

    grupo = grupo_gerentes()
    if grupo is None or not renova.loja_id:
        return []
    return list(grupo.members.filter(is_active=True, sector_id=renova.loja_id).order_by('first_name', 'last_name'))


def avisar_gerentes(renova, autor):
    """Avaliação nova: o sino dos gerentes da loja pede a aprovação. Devolve quem foi avisado."""
    gerentes = [g for g in gerentes_da_loja(renova) if g.pk != getattr(autor, 'pk', None)]
    _avisar_no_sino(gerentes, f'Renova {renova.codigo}: aprovar a troca',
                    f'{renova.vendedor_nome} avaliou {renova.aparelho} — padrão {renova.padrao or "—"}, '
                    f'{_moeda(renova.valor_estimado)}. Aprove ou reprove.',
                    reverse('renova:detalhe', args=[renova.pk]), autor)
    return gerentes


def decidir(renova, gerente, aprovar, observacao=''):
    """Aprovação do gerente. Aprovada: abre o chamado (e libera a etiqueta). Reprovada: sem troca.

    Devolve (renova, chamado). O chamado é None na reprovação ou se não abrir — a
    aprovação vale mesmo assim, e dá para abrir de novo pela tela.
    """
    observacao = (observacao or '').strip()[:2000]
    if not aprovar and not observacao:
        raise DecisaoInvalida('Diga ao vendedor por que a troca foi reprovada.')
    with transaction.atomic():
        renova = Renova.objects.select_for_update().get(pk=renova.pk)
        if renova.aprovacao != Renova.AGUARDANDO_GERENTE:
            raise DecisaoInvalida(f'{renova.codigo} já foi decidida: {renova.get_aprovacao_display().lower()}.')
        renova.aprovacao = Renova.APROVADA if aprovar else Renova.REPROVADA
        renova.aprovacao_por, renova.aprovacao_em, renova.aprovacao_obs = gerente, timezone.now(), observacao
        campos = ['aprovacao', 'aprovacao_por', 'aprovacao_em', 'aprovacao_obs', 'atualizado_em']
        if not aprovar:
            renova.parecer = checklist.NAO_APROVADO
            campos.append('parecer')
        renova.save(update_fields=campos)

    chamado = None
    if aprovar:
        try:
            chamado = abrir_chamado(renova, renova.criado_por or gerente)
        except Exception:                                       # noqa: BLE001 — a aprovação já valeu
            logger.exception('Chamado do Renova %s não abriu na aprovação', renova.pk)

    if renova.criado_por_id and renova.criado_por_id != gerente.pk:
        if aprovar:
            texto = (f'{_nome(gerente)} aprovou a troca de {renova.aparelho}. '
                     + (f'Chamado #{chamado.pk} aberto — imprima a etiqueta.' if chamado else 'Imprima a etiqueta.'))
        else:
            texto = f'{_nome(gerente)} reprovou a troca de {renova.aparelho}: {observacao}'
        _avisar_no_sino(renova.criado_por, f'Renova {renova.codigo}: {renova.get_aprovacao_display().lower()}',
                        texto, reverse('renova:detalhe', args=[renova.pk]), gerente)
    return renova, chamado


def aprovado_para_receber(renova):
    """Não aprovado não viaja: fica fora da fila de chegada."""
    return renova.parecer != checklist.NAO_APROVADO
