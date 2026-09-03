"""Ponte entre a agenda e as tarefas do portal.

Quando alguém marca uma **Tarefa** na agenda, o esperado é que ela vire tarefa
de verdade — a mesma que aparece em /users/tasks/, com status, chat, anexos e
subtarefas. Não um compromisso no calendário que só parece uma tarefa.

Mesmo desenho da ponte de chamada (reunioes/servicos.py): a agenda pede, o
outro módulo resolve, e falhar aqui nunca derruba a criação do evento. Perder o
compromisso por causa da tarefa seria o pior dos dois mundos.
"""
import logging

logger = logging.getLogger(__name__)

# Tipo de evento da agenda que vira tarefa de verdade.
TIPOS_COM_TAREFA = ('task',)


def precisa_de_tarefa(evento):
    return getattr(evento, 'event_type', '') in TIPOS_COM_TAREFA


def tarefa_para_evento(evento, convidados=(), autor=None):
    """Garante a tarefa do evento e devolve ela (ou None se algo falhar).

    Idempotente: chamar de novo no mesmo evento não cria uma segunda tarefa,
    só atualiza título, descrição e prazo — evento remarcado não pode virar
    duas tarefas iguais na lista de ninguém.

    O prazo é o **início** do evento, não o fim: é a hora em que a pessoa
    combinou de fazer aquilo, e é por ela que /users/tasks/ separa o dia.
    """
    from core.models import TaskActivity

    try:
        dono = getattr(evento, 'owner', None)
        if dono is None:
            return None

        dados = {
            'title': (evento.title or 'Tarefa')[:200],
            'description': evento.description or '',
            'due_date': evento.start,
        }

        tarefa = getattr(evento, 'tarefa', None)
        if tarefa is None:
            tarefa = TaskActivity.objects.create(
                assigned_to=dono, created_by=(autor or dono),
                priority='MEDIUM', status='PENDING', **dados)
            evento.tarefa = tarefa
            evento.save(update_fields=['tarefa'])
        else:
            for campo, valor in dados.items():
                setattr(tarefa, campo, valor)
            tarefa.save(update_fields=list(dados))

        # Cada convidado ganha a própria tarefa: tarefa é de quem faz, e uma
        # só, compartilhada, não teria como ter status por pessoa.
        _tarefas_dos_convidados(evento, tarefa, convidados, autor or dono)
        return tarefa
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Tarefa do evento %s não criada: %s',
                       getattr(evento, 'pk', '?'), exc)
        return None


def _tarefas_dos_convidados(evento, tarefa_dona, convidados, autor):
    """Uma cópia da tarefa para cada convidado, sem duplicar em remarcação."""
    from core.models import TaskActivity

    for pessoa in convidados or ():
        if pessoa is None or pessoa.pk == tarefa_dona.assigned_to_id:
            continue
        existente = TaskActivity.objects.filter(
            assigned_to=pessoa, created_by=autor,
            title=tarefa_dona.title, due_date=evento.start).first()
        if existente:
            continue
        TaskActivity.objects.create(
            assigned_to=pessoa, created_by=autor,
            title=tarefa_dona.title, description=tarefa_dona.description,
            due_date=evento.start, priority='MEDIUM', status='PENDING')


DIAS_DA_SEMANA = ('segunda', 'terça', 'quarta', 'quinta',
                  'sexta', 'sábado', 'domingo')


def rotulo_repeticao(data):
    """"Repetir toda segunda" — o dia da semana da data escolhida.

    O rótulo genérico "Semanal" obrigava a pessoa a conferir no calendário em
    que dia ela estava marcando.
    """
    if data is None:
        return 'Repetir toda semana'
    nome = DIAS_DA_SEMANA[data.weekday()]
    artigo = 'todo' if data.weekday() >= 5 else 'toda'
    return f'Repetir {artigo} {nome}'
