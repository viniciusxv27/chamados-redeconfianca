import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from datetime import date
from django.db.models import Q
from checklists.models import ChecklistExecution, ChecklistAssignment
from users.models import User, Sector

today = date.today()

print(f"📅 Data de hoje: {today}")
print("=" * 80)

# Total de execuções hoje
total_today = ChecklistExecution.objects.filter(execution_date=today).count()
print(f"\n📊 Total de execuções hoje: {total_today}")

if total_today > 0:
    # Agrupar por setor
    executions = ChecklistExecution.objects.filter(
        execution_date=today
    ).select_related('assignment__assigned_to__sector', 'assignment__template')
    
    sectors_dict = {}
    for execution in executions:
        sector_name = execution.assignment.assigned_to.sector.name if execution.assignment.assigned_to.sector else "Sem setor"
        if sector_name not in sectors_dict:
            sectors_dict[sector_name] = []
        sectors_dict[sector_name].append(execution)
    
    print(f"\n📍 Execuções por setor:")
    for sector_name, execs in sorted(sectors_dict.items()):
        print(f"\n   {sector_name}: {len(execs)} execuções")
        # Mostrar primeira execução como exemplo
        if execs:
            ex = execs[0]
            print(f"      Exemplo: {ex.assignment.template.name}")
            print(f"      Usuário: {ex.assignment.assigned_to.get_full_name()}")

# Verificar supervisor com setor Financeiro
print("\n" + "=" * 80)
print("🔍 Testando supervisor do setor Financeiro:")

financeiro_sector = Sector.objects.filter(name="Financeiro").first()
if financeiro_sector:
    print(f"   ✅ Setor encontrado: {financeiro_sector.name}")
    
    # Execuções do setor financeiro hoje
    financeiro_execs = ChecklistExecution.objects.filter(
        Q(assignment__assigned_to__sector=financeiro_sector) |
        Q(assignment__assigned_to__sectors=financeiro_sector),
        execution_date=today
    ).distinct()
    
    print(f"   📋 Execuções do setor Financeiro hoje: {financeiro_execs.count()}")
    
    if financeiro_execs.exists():
        print(f"\n   Primeiras 3 execuções:")
        for execution in financeiro_execs[:3]:
            print(f"      - {execution.assignment.template.name}")
            print(f"        Usuário: {execution.assignment.assigned_to.get_full_name()}")
            print(f"        Status: {execution.status}")
    
    # Buscar supervisor do setor financeiro
    supervisors = User.objects.filter(
        Q(hierarchy='SUPERVISOR') & (Q(sector=financeiro_sector) | Q(sectors=financeiro_sector))
    ).distinct()
    
    print(f"\n   👥 Supervisores do setor Financeiro: {supervisors.count()}")
    for sup in supervisors:
        print(f"      - {sup.get_full_name()}")
        
        # Testar query do supervisor
        user_sectors = list(sup.sectors.all())
        if sup.sector:
            user_sectors.append(sup.sector)
        
        sup_execs = ChecklistExecution.objects.filter(
            Q(assignment__assigned_to=sup) |
            Q(assignment__assigned_to__sector__in=user_sectors) |
            Q(assignment__assigned_to__sectors__in=user_sectors),
            execution_date=today
        ).distinct()
        
        print(f"        Pode ver {sup_execs.count()} execuções hoje")
else:
    print("   ❌ Setor Financeiro não encontrado")
