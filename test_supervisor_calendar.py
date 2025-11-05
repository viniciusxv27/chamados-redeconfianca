import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from datetime import date
from django.db.models import Q
from checklists.models import ChecklistExecution
from users.models import User

# Buscar um usuário supervisor
supervisors = User.objects.filter(hierarchy='SUPERVISOR').first()

if not supervisors:
    print("❌ Nenhum supervisor encontrado no sistema")
    exit()

print(f"✅ Testando com supervisor: {supervisors.get_full_name()}")
print(f"   Email: {supervisors.email}")
print(f"   Setor principal: {supervisors.sector.name if supervisors.sector else 'Sem setor'}")

# Listar setores do supervisor
user_sectors = list(supervisors.sectors.all())
if supervisors.sector:
    user_sectors.append(supervisors.sector)

print(f"\n📍 Setores do supervisor ({len(user_sectors)}):")
for sector in user_sectors:
    print(f"   - {sector.name}")

# Testar a query que está sendo usada na API
today = date.today()

print(f"\n🔍 Testando query para data: {today}")

# Query original (antiga - errada)
old_executions = ChecklistExecution.objects.filter(
    Q(assignment__assigned_to=supervisors) | Q(assignment__assigned_by=supervisors),
    execution_date=today
).distinct()

print(f"\n❌ Query ANTIGA (só quem ele atribuiu):")
print(f"   Total de execuções: {old_executions.count()}")

# Query nova (correta - por setor)
new_executions = ChecklistExecution.objects.filter(
    Q(assignment__assigned_to=supervisors) |
    Q(assignment__assigned_to__sector__in=user_sectors) |
    Q(assignment__assigned_to__sectors__in=user_sectors),
    execution_date=today
).distinct()

print(f"\n✅ Query NOVA (por setor):")
print(f"   Total de execuções: {new_executions.count()}")

if new_executions.exists():
    print(f"\n📋 Primeiras 5 execuções:")
    for execution in new_executions[:5]:
        print(f"   - {execution.assignment.template.name}")
        print(f"     Atribuído a: {execution.assignment.assigned_to.get_full_name()}")
        print(f"     Setor: {execution.assignment.assigned_to.sector.name if execution.assignment.assigned_to.sector else 'Sem setor'}")
        print(f"     Status: {execution.status}")
        print(f"     Período: {execution.period}")
        print()

print(f"\n📊 Diferença: {new_executions.count() - old_executions.count()} execuções a mais com a nova query")
print(f"\n✅ Teste concluído! Supervisores agora podem ver checklists dos seus setores no calendário.")
