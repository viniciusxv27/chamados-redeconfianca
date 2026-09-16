"""Data de conclusão das tarefas que já estavam concluídas.

O portal não guardava quando a tarefa foi concluída. A data dada a elas é a
maior entre o prazo, o dia em que a tarefa foi criada e o início do primeiro
ciclo — nunca depois de agora. Assim:

- quem tinha prazo dentro de um ciclo continua pontuando no mesmo mês;
- a entrega de antes do primeiro ciclo (tarefa criada com prazo já vencido)
  passa a pontuar no primeiro mês do ciclo, em vez de em mês nenhum.

Não depende do dia em que a migração roda.
"""
from datetime import datetime, time

from django.db import migrations
from django.utils import timezone


def preencher(apps, schema_editor):
    Tarefa = apps.get_model('impulso', 'TarefaProjeto')
    Ciclo = apps.get_model('impulso', 'Ciclo')

    primeiro = Ciclo.objects.order_by('inicio').values_list('inicio', flat=True).first()
    agora = timezone.now()
    fuso = timezone.get_current_timezone()
    for tarefa in Tarefa.objects.filter(status='CONCLUIDA', concluida_em__isnull=True):
        criada = timezone.localtime(tarefa.criado_em, fuso).date() if tarefa.criado_em else None
        dia = max(d for d in (tarefa.prazo, criada, primeiro) if d is not None) if (
            tarefa.prazo or criada or primeiro) else None
        if dia is None:
            quando = agora
        else:
            quando = min(timezone.make_aware(datetime.combine(dia, time(12)), fuso), agora)
        Tarefa.objects.filter(pk=tarefa.pk).update(concluida_em=quando)


class Migration(migrations.Migration):

    dependencies = [
        ('impulso', '0018_tarefa_concluida_em'),
    ]

    operations = [
        migrations.RunPython(preencher, migrations.RunPython.noop),
    ]
