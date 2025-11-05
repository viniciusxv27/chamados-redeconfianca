import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from datetime import date
from django.db.models import Q
from checklists.models import ChecklistExecution, ChecklistTemplate
from users.models import User, Sector

today = date.today()

print(f"📅 Data de hoje: {today}")
print("=" * 80)

# Buscar setor Financeiro
financeiro = Sector.objects.filter(name="Financeiro").first()
if not financeiro:
    print("❌ Setor Financeiro não encontrado")
    exit()

print(f"✅ Setor Financeiro encontrado")

# Verificar templates do setor Financeiro
templates_financeiro = ChecklistTemplate.objects.filter(sector=financeiro, is_active=True)
print(f"\n📋 Templates do setor Financeiro: {templates_financeiro.count()}")
for template in templates_financeiro:
    print(f"   - {template.name}")

# Verificar execuções de templates do setor Financeiro hoje
print(f"\n🔍 Execuções de templates do Financeiro hoje:")

executions_by_template = ChecklistExecution.objects.filter(
    assignment__template__sector=financeiro,
    execution_date=today
).select_related('assignment__template', 'assignment__assigned_to')

print(f"   Total: {executions_by_template.count()}")

if executions_by_template.exists():
    print(f"\n   Primeiras 5 execuções:")
    for execution in executions_by_template[:5]:
        print(f"      • Template: {execution.assignment.template.name}")
        print(f"        Atribuído a: {execution.assignment.assigned_to.get_full_name()}")
        print(f"        Setor do usuário: {execution.assignment.assigned_to.sector.name if execution.assignment.assigned_to.sector else 'Sem setor'}")
        print(f"        Status: {execution.status}")
        print()

# Buscar supervisor do setor Financeiro
print("=" * 80)
print("👥 Supervisores do setor Financeiro:")

supervisors = User.objects.filter(
    Q(hierarchy='SUPERVISOR') & (Q(sector=financeiro) | Q(sectors=financeiro))
).distinct()

if not supervisors.exists():
    print("   ❌ Nenhum supervisor encontrado")
else:
    for sup in supervisors:
        print(f"\n   👤 {sup.get_full_name()}")
        print(f"      Email: {sup.email}")
        
        # Listar setores
        user_sectors = list(sup.sectors.all())
        if sup.sector:
            user_sectors.append(sup.sector)
        
        print(f"      Setores ({len(user_sectors)}):")
        for sector in user_sectors:
            print(f"         - {sector.name}")
        
        # Testar query ANTIGA (por usuário)
        old_query = ChecklistExecution.objects.filter(
            Q(assignment__assigned_to=sup) |
            Q(assignment__assigned_to__sector__in=user_sectors) |
            Q(assignment__assigned_to__sectors__in=user_sectors),
            execution_date=today
        ).distinct()
        
        print(f"\n      ❌ Query ANTIGA (por setor do usuário):")
        print(f"         Total: {old_query.count()} execuções")
        
        # Testar query NOVA (por template)
        new_query = ChecklistExecution.objects.filter(
            Q(assignment__assigned_to=sup) | Q(assignment__template__sector__in=user_sectors),
            execution_date=today
        )
        
        print(f"\n      ✅ Query NOVA (por setor do template):")
        print(f"         Total: {new_query.count()} execuções")
        
        if new_query.exists():
            print(f"\n         Primeiras 3 execuções:")
            for execution in new_query[:3]:
                print(f"            • {execution.assignment.template.name}")
                print(f"              Para: {execution.assignment.assigned_to.get_full_name()}")
                print(f"              Setor do template: {execution.assignment.template.sector.name}")
        
        print(f"\n      📊 Diferença: +{new_query.count() - old_query.count()} execuções")

print("\n" + "=" * 80)
print("✅ Teste concluído!")
