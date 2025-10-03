#!/usr/bin/env python3
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from users.models import User

def get_test_user():
    """Retorna um usuário de teste para simular o acesso"""
    print("👥 Usuários disponíveis para teste:")
    print("=" * 50)
    
    users = User.objects.filter(is_active=True).order_by('hierarchy', 'email')[:10]
    
    for i, user in enumerate(users, 1):
        print(f"{i:2d}. {user.get_full_name()} ({user.email})")
        print(f"    Hierarquia: {user.hierarchy}, Setor: {user.sector}")
        print()
    
    print("Para testar o acesso ao checklist ID 94:")
    print("1. Faça login como um dos usuários acima")
    print("2. Acesse: http://localhost:8000/users/checklist/94/")
    print()
    print("Usuários com acesso esperado ao checklist ID 94:")
    print("- TAMIRES SILVA (própria usuária)")
    print("- MAICON ALEX DE MIRANDA (criador)")
    print("- Qualquer SUPERADMIN ou ADMIN")
    print("- Supervisores do setor Financeiro")

if __name__ == "__main__":
    get_test_user()