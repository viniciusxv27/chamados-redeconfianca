"""Chamado de suporte → meta no Impulso de quem assumiu, e o fechamento do chamado conclui a meta.

"Puxar para impulso e assumir" (/projects/support/admin/template/) cria uma meta já aprovada, Em Andamento,
no Kanban do atendente. Quando o chamado é fechado — por qualquer caminho que salve o SupportChat — a meta vai
para Concluída sem notas (conta como concluída; a nota de qualidade é só da avaliação de um gestor). Se o chamado
reabrir (avaliação ruim do solicitante), a meta que ele tinha concluído volta para Em Andamento.
"""
import logging

from django.db import transaction
from django.utils import timezone

from .models import ChamadoNoImpulso, Meta
from .utils import e_superadmin, get_gestores_do_setor, is_impulso_manager, is_impulso_member

logger = logging.getLogger(__name__)

PRIORIDADE = {'URGENTE': Meta.Prioridade.CRITICA, 'ALTA': Meta.Prioridade.ALTA,
              'MEDIA': Meta.Prioridade.MEDIA, 'BAIXA': Meta.Prioridade.BAIXA}
FECHADO = 'FECHADO'


def pode_puxar(user):
    """Só quem tem Kanban no Impulso: sem isso, a meta seria criada num lugar que a pessoa não abre."""
    return is_impulso_member(user)


def meta_aberta(chamado, user):
    """A meta que esta pessoa já tem deste chamado e ainda não foi concluída (ou None)."""
    vinculo = (ChamadoNoImpulso.objects.select_related('meta')
               .filter(chamado=chamado, meta__colaborador=user).exclude(meta__status=Meta.Status.CONCLUIDA)
               .order_by('-puxado_em').first())
    return vinculo.meta if vinculo else None


def _gestor_para(user):
    """Quem avalia a meta: a própria pessoa se ela é gestora/SUPERADMIN; senão o gestor do setor dela."""
    if e_superadmin(user) or is_impulso_manager(user):
        return user
    return get_gestores_do_setor(user).order_by('first_name', 'last_name').first() or user


def _descricao(chamado):
    quem = chamado.user.get_full_name() or chamado.user.email
    partes = [f'Chamado de suporte {chamado.protocol} aberto por {quem}'
              + (f' ({chamado.user.sector.name})' if chamado.user.sector_id else '')
              + f' em {timezone.localtime(chamado.created_at):%d/%m/%Y %H:%M}.']
    if chamado.sector_id:
        partes.append(f'Setor: {chamado.sector.name}' + (f' · Categoria: {chamado.category.name}'
                                                           if getattr(chamado, 'category_id', None) else ''))
    partes.append('Fecha sozinha quando o chamado for fechado.')
    partes.append(f'Abrir o chamado: /projects/support/admin/template/?chat={chamado.id}')
    return '\n'.join(partes)


@transaction.atomic
def puxar(chamado, user):
    """Cria (ou devolve a que já existe) a meta do chamado no Impulso de `user`. Devolve (meta, criada)."""
    existente = meta_aberta(chamado, user)
    if existente is not None:
        return existente, False
    meta = Meta.objects.create(
        gestor=_gestor_para(user), colaborador=user, created_by=user,
        titulo=f'Chamado {chamado.protocol} — {chamado.title}'[:200],
        descricao=_descricao(chamado), prazo=timezone.localdate(),
        prioridade=PRIORIDADE.get(chamado.priority or '', Meta.Prioridade.MEDIA),
        recorrencia=Meta.Recorrencia.UNICA, recorrencia_ativa=False,
        aprovacao=Meta.Aprovacao.APROVADA, decidida_por=user, decidida_em=timezone.now(),
        status=Meta.Status.EM_ANDAMENTO)
    ChamadoNoImpulso.objects.create(chamado=chamado, meta=meta, puxado_por=user)
    return meta, True


def ao_salvar_chamado(chamado):
    """Sinal do SupportChat: fechado → conclui as metas abertas dele; reaberto → reabre as que ele concluiu."""
    vinculos = list(ChamadoNoImpulso.objects.select_related('meta').filter(chamado_id=chamado.pk))
    if not vinculos:
        return
    agora = timezone.now()
    for v in vinculos:
        meta = v.meta
        if chamado.status == FECHADO and meta.status != Meta.Status.CONCLUIDA:
            quando = timezone.localtime(chamado.closed_at or agora)
            meta.status = Meta.Status.CONCLUIDA
            meta.entregue_em = meta.entregue_em or agora
            meta.avaliado_em = agora
            meta.avaliacao_comentario = (f'Concluída automaticamente: o chamado {chamado.protocol} foi fechado '
                                         f'em {quando:%d/%m/%Y %H:%M}.')
            meta.save(update_fields=['status', 'entregue_em', 'avaliado_em', 'avaliacao_comentario', 'updated_at'])
            ChamadoNoImpulso.objects.filter(pk=v.pk).update(concluida_pelo_chamado=True)
        elif chamado.status != FECHADO and v.concluida_pelo_chamado and meta.status == Meta.Status.CONCLUIDA:
            meta.status = Meta.Status.EM_ANDAMENTO
            meta.entregue_em = meta.avaliado_em = None
            meta.avaliacao_comentario = f'O chamado {chamado.protocol} foi reaberto: a meta voltou para Em Andamento.'
            meta.save(update_fields=['status', 'entregue_em', 'avaliado_em', 'avaliacao_comentario', 'updated_at'])
            ChamadoNoImpulso.objects.filter(pk=v.pk).update(concluida_pelo_chamado=False)


def conectar():
    from django.db.models.signals import post_save

    from projects.models_chat import SupportChat

    def _sinal(sender, instance, **kwargs):
        try:
            ao_salvar_chamado(instance)
        except Exception:  # noqa: BLE001 — a meta não pode impedir o chamado de salvar
            logger.exception('Impulso: não deu para atualizar a meta do chamado %s', instance.pk)

    # weak=False: _sinal é uma função local e, com referência fraca, sumiria assim que conectar() terminasse.
    post_save.connect(_sinal, sender=SupportChat, weak=False, dispatch_uid='impulso_chamado_suporte')
