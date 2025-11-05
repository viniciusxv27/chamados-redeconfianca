import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from datetime import date
from checklists.models import ChecklistExecution, ChecklistTaskExecution

today = date.today()

print(f"🔧 Corrigindo execuções do template 24 para hoje ({today})")
print("=" * 80)

# Buscar todas as execuções do template 24 para hoje
executions = ChecklistExecution.objects.filter(
    assignment__template_id=24,
    execution_date=today
).select_related('assignment__template', 'assignment__assigned_to')

total = executions.count()
fixed = 0

print(f"📊 Total de execuções encontradas: {total}\n")

for execution in executions:
    # Verificar se tem task_executions
    task_count = ChecklistTaskExecution.objects.filter(execution=execution).count()
    template_tasks = execution.assignment.template.tasks.count()
    
    if task_count == 0 and template_tasks > 0:
        print(f"🔧 Corrigindo execução ID {execution.id}")
        print(f"   Usuário: {execution.assignment.assigned_to.get_full_name()}")
        print(f"   Período: {execution.period}")
        
        # Criar task_executions
        for task in execution.assignment.template.tasks.all():
            ChecklistTaskExecution.objects.create(
                execution=execution,
                task=task
            )
        
        print(f"   ✅ Criadas {template_tasks} task_executions\n")
        fixed += 1
    elif task_count < template_tasks:
        print(f"⚠️ Execução ID {execution.id} tem {task_count}/{template_tasks} tasks")
        print(f"   Usuário: {execution.assignment.assigned_to.get_full_name()}")
        
        # Verificar quais tasks faltam
        existing_task_ids = set(
            ChecklistTaskExecution.objects.filter(execution=execution).values_list('task_id', flat=True)
        )
        
        created = 0
        for task in execution.assignment.template.tasks.all():
            if task.id not in existing_task_ids:
                ChecklistTaskExecution.objects.create(
                    execution=execution,
                    task=task
                )
                created += 1
        
        print(f"   ✅ Criadas {created} task_executions faltantes\n")
        fixed += 1

print("=" * 80)
print(f"✅ Processo concluído!")
print(f"   Total de execuções: {total}")
print(f"   Execuções corrigidas: {fixed}")
