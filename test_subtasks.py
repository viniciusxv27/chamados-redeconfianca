#!/usr/bin/env python3
"""
Script para testar as funcionalidades de subtarefas
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from projects.models import Project, Activity
from users.models import User

def test_subtask_functionality():
    """Testa as funcionalidades de subtarefas"""
    
    print("=== Teste das Funcionalidades de Subtarefas ===\n")
    
    # Verificar se existem projetos
    projects = Project.objects.all()
    if not projects.exists():
        print("❌ Nenhum projeto encontrado. Crie um projeto primeiro.")
        return
    
    project = projects.first()
    print(f"✅ Usando projeto: {project.name}")
    
    # Verificar atividades raiz (não subtarefas)
    root_activities = project.activities.filter(parent_activity__isnull=True)
    print(f"📋 Atividades raiz no projeto: {root_activities.count()}")
    
    # Verificar subtarefas
    subtasks = project.activities.filter(parent_activity__isnull=False)
    print(f"📝 Subtarefas no projeto: {subtasks.count()}")
    
    if root_activities.exists():
        activity = root_activities.first()
        print(f"\n🔍 Analisando atividade: {activity.name}")
        print(f"   - Pode ter filhos: {activity.can_have_children}")
        print(f"   - Nível hierárquico: {activity.hierarchy_level}")
        print(f"   - Subtarefas: {activity.sub_activities.count()}")
        
        # Listar subtarefas
        for i, subtask in enumerate(activity.sub_activities.all(), 1):
            print(f"   {i}. {subtask.name}")
    
    print(f"\n📊 Resumo:")
    print(f"   - Total de atividades: {project.activities.count()}")
    print(f"   - Atividades raiz: {root_activities.count()}")
    print(f"   - Subtarefas: {subtasks.count()}")
    
    # Testar filtros do Kanban
    print(f"\n🏗️ Teste dos filtros do Kanban:")
    for status_code, status_name in Activity.STATUS_CHOICES:
        kanban_count = project.activities.filter(
            status=status_code, 
            parent_activity__isnull=True
        ).count()
        total_count = project.activities.filter(status=status_code).count()
        print(f"   - {status_name}: {kanban_count} no kanban (de {total_count} totais)")

if __name__ == "__main__":
    test_subtask_functionality()