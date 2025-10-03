#!/usr/bin/env python3
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from core.models import DailyChecklist
from users.models import User
from django.db import models

def test_permissions():
    print("🔍 TESTE: Permissões para checklist ID 94")
    print("=" * 60)
    
    # Buscar o checklist
    try:
        checklist = DailyChecklist.objects.get(id=94)
        print(f"📋 Checklist: {checklist.title}")
        print(f"👤 Usuário: {checklist.user} (Setor: {checklist.user.sector})")
        print(f"👨‍💼 Criado por: {checklist.created_by} (Setor: {checklist.created_by.sector if checklist.created_by else 'N/A'})")
        
        print(f"\n🔐 Testando permissões para diferentes usuários:")
        
        # Testar com alguns usuários
        users_to_test = [
            checklist.user,  # Próprio usuário
            checklist.created_by,  # Quem criou
        ]
        
        # Adicionar mais usuários para teste
        additional_users = User.objects.filter(
            hierarchy__in=['SUPERADMIN', 'ADMIN', 'SUPERVISOR']
        ).exclude(
            id__in=[u.id for u in users_to_test if u]
        )[:3]
        
        users_to_test.extend(additional_users)
        
        for test_user in users_to_test:
            if not test_user:
                continue
                
            print(f"\n👤 Testando com: {test_user} ({test_user.hierarchy})")
            print(f"   Setor: {test_user.sector}")
            
            # Simular a lógica da view
            has_access = False
            
            if test_user.hierarchy in ['SUPERADMIN', 'ADMIN']:
                has_access = True
                reason = "SuperAdmin/Admin - acesso total"
            
            elif test_user.hierarchy in ['SUPERVISOR', 'ADMINISTRATIVO'] or test_user.is_staff:
                # Verificar se satisfaz alguma condição
                conditions = []
                if checklist.user == test_user:
                    conditions.append("próprio checklist")
                if checklist.created_by == test_user:
                    conditions.append("criou o checklist")
                if checklist.user.sector == test_user.sector:
                    conditions.append("mesmo setor")
                
                if conditions:
                    has_access = True
                    reason = f"Supervisor - {', '.join(conditions)}"
                else:
                    reason = "Supervisor - sem acesso (setor diferente)"
            
            else:
                if checklist.user == test_user:
                    has_access = True
                    reason = "Usuário comum - próprio checklist"
                else:
                    reason = "Usuário comum - não é seu checklist"
            
            status = "✅ PERMITIDO" if has_access else "❌ NEGADO"
            print(f"   {status}: {reason}")
            
    except DailyChecklist.DoesNotExist:
        print("❌ Checklist ID 94 não encontrado!")

if __name__ == "__main__":
    test_permissions()