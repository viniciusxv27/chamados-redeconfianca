#!/usr/bin/env python3
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from core.models import DailyChecklist
from users.models import User

def debug_checklist():
    print("🔍 DEBUG: Investigando problema com checklist ID 94")
    print("=" * 60)
    
    # Verificar se existe checklist com ID 94
    try:
        checklist = DailyChecklist.objects.get(id=94)
        print(f"✅ Checklist ID 94 encontrado:")
        print(f"   - Título: {checklist.title}")
        print(f"   - Usuário: {checklist.user}")
        print(f"   - Data: {checklist.date}")
        print(f"   - Criado por: {checklist.created_by}")
        print(f"   - Setor do usuário: {checklist.user.sector}")
        print(f"   - Setor do criador: {checklist.created_by.sector if checklist.created_by else 'N/A'}")
    except DailyChecklist.DoesNotExist:
        print("❌ Checklist ID 94 não encontrado!")
        
        # Listar alguns checklists existentes
        print("\n📋 Checklists existentes (últimos 10):")
        recent_checklists = DailyChecklist.objects.all().order_by('-id')[:10]
        for cl in recent_checklists:
            print(f"   - ID {cl.id}: {cl.title} (User: {cl.user}, Date: {cl.date})")
    
    # Verificar total de checklists
    total = DailyChecklist.objects.count()
    print(f"\n📊 Total de checklists no sistema: {total}")
    
    # Verificar usuários
    print(f"\n👥 Total de usuários: {User.objects.count()}")
    
    if total == 0:
        print("⚠️  Não há checklists no sistema! Isso pode explicar o erro 404.")
    
if __name__ == "__main__":
    debug_checklist()