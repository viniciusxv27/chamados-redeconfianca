"""Tarefa criada pela ata de uma reunião (agenda) → meta do Impulso.

Na tela da transcrição, quem é do Impulso importa uma tarefa como meta para si,
escolhendo o gestor. Valem as regras de criar meta pela tela do Impulso:

- Colaborador: o gestor precisa dividir um setor com ele, e a pessoa decide se a
  atividade passa pela aprovação (padrão: passa).
- Gestor do Impulso: escolhe qualquer gestor, inclusive a si mesmo, e a meta já
  nasce aprovada — é atividade dele.

A meta guarda de qual tarefa veio: a mesma pessoa não importa a mesma tarefa
duas vezes enquanto a primeira vale ou aguarda aprovação (recusada, pode de novo).
"""
from datetime import date

from django.db import transaction
from django.utils import timezone

from .models import Meta
from .utils import get_gestores, get_gestores_do_setor, is_impulso_manager, is_impulso_member


class ImportacaoRecusada(Exception):
    """A importação não aconteceu; a mensagem é para a pessoa."""

    def __init__(self, mensagem, status=400, **extra):
        super().__init__(mensagem)
        self.status = status
        self.extra = extra


def gestores_para(user):
    """Quem pode ficar como gestor da meta que `user` importa."""
    if is_impulso_manager(user):
        return get_gestores()
    return get_gestores_do_setor(user)


def importacoes_vivas(user, tarefa_ids):
    """{tarefa_id: meta} das importações desta pessoa que ainda valem."""
    metas = (Meta.objects
             .filter(colaborador=user, tarefa_origem_id__in=list(tarefa_ids))
             .exclude(aprovacao=Meta.Aprovacao.RECUSADA)
             .order_by('-id'))
    vivas = {}
    for meta in metas:
        vivas.setdefault(meta.tarefa_origem_id, meta)
    return vivas


def importar_tarefa(user, tarefa, *, gestor_id, prazo, titulo, descricao,
                    precisa_aprovacao=True, origem=''):
    """Cria a meta a partir da tarefa. Devolve a meta ou levanta ImportacaoRecusada."""
    if not is_impulso_member(user):
        raise ImportacaoRecusada('Você não participa do Impulso.', status=403)

    titulo = (titulo or '').strip()[:200]
    descricao = (descricao or '').strip()
    if not titulo or not descricao:
        raise ImportacaoRecusada('Preencha o título e a descrição da meta.')
    if not isinstance(prazo, date):
        raise ImportacaoRecusada('Escolha o prazo da meta.')
    # O `min` do campo de data é só sugestão do navegador: meta que nasce
    # vencida entraria no Kanban já atrasada.
    if prazo < timezone.localdate():
        raise ImportacaoRecusada('O prazo não pode ser anterior a hoje.')

    sou_gestor = is_impulso_manager(user)
    gestor = gestores_para(user).filter(id=gestor_id).first() if gestor_id else None
    if gestor is None:
        raise ImportacaoRecusada('Escolha o gestor responsável.' if sou_gestor
                                 else 'Escolha um gestor do seu setor.')

    aprovada = sou_gestor or not precisa_aprovacao
    with transaction.atomic():
        # Trava a tarefa: clique duplo (ou duas abas) não cria duas metas.
        list(type(tarefa).objects.select_for_update().filter(pk=tarefa.pk).values_list('pk', flat=True))
        existente = importacoes_vivas(user, [tarefa.pk]).get(tarefa.pk)
        if existente is not None:
            raise ImportacaoRecusada(
                'Você já importou esta tarefa para o Impulso.', status=409, meta_id=existente.id,
                aprovada=existente.aprovacao == Meta.Aprovacao.APROVADA)
        meta = Meta.objects.create(
            gestor=gestor,
            colaborador=user,
            titulo=titulo,
            descricao=descricao,
            recorrencia=Meta.Recorrencia.UNICA,
            prazo=prazo,
            aprovacao=Meta.Aprovacao.APROVADA if aprovada else Meta.Aprovacao.PENDENTE,
            solicitada_por=None if sou_gestor else user,
            created_by=user,
            tarefa_origem=tarefa,
        )
    _avisar_gestor(user, gestor, meta, sou_gestor, aprovada, origem)
    return meta


def _avisar_gestor(user, gestor, meta, sou_gestor, aprovada, origem):
    """Os mesmos avisos de criar meta pela tela do Impulso."""
    from .views import _notify   # tardio: as views do Impulso são pesadas

    quem = user.get_full_name() or user.email
    de_onde = f' (da reunião "{origem}")' if origem else ''
    link = f'/impulso/metas/{meta.id}/'
    if sou_gestor:
        if gestor.id != user.id:
            _notify([gestor], 'Meta criada no seu nome',
                    f'{quem} importou a atividade "{meta.titulo}"{de_onde} com você como gestor '
                    f'responsável. A avaliação no fim é sua.', link)
    elif not aprovada:
        _notify([gestor], 'Nova solicitação de meta',
                f'{quem} pediu a meta "{meta.titulo}"{de_onde}. Aprove ou recuse.', link)
    else:
        # Avisa mesmo sem pedir nada: o gestor avalia a meta no fim e não pode ser
        # pego de surpresa por uma atividade que apareceu sozinha.
        _notify([gestor], 'Nova atividade criada pelo colaborador',
                f'{quem} criou "{meta.titulo}"{de_onde} sem pedir autorização. '
                f'A avaliação no fim continua sendo sua.', link)
