#!/usr/bin/env python
"""
Script para corrigir o created_by dos chamados restaurados.
Verifica nas notificações push (notifications_pushnotification) quem realmente abriu cada chamado.
"""

import os
import sys
import django

# Configurar Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.db import transaction
from notifications.models import PushNotification
from tickets.models import Ticket
from users.models import User


def analyze_tickets_to_fix(lucas_id=3, dry_run=True):
    """
    Analisa os chamados que precisam ser corrigidos.
    
    Args:
        lucas_id: ID do usuário Lucas (quem ficou incorretamente como created_by)
        dry_run: Se True, apenas mostra o que seria feito. Se False, aplica as correções.
    """
    print("=" * 80)
    print("ANÁLISE DE CHAMADOS PARA CORREÇÃO DO CREATED_BY")
    print("=" * 80)
    
    # Buscar o Lucas
    try:
        lucas = User.objects.get(id=lucas_id)
        print(f"\nUsuário incorreto: ID={lucas.id}, Nome={lucas.get_full_name()}")
    except User.DoesNotExist:
        print(f"Usuário com ID {lucas_id} não encontrado!")
        return
    
    # Buscar todos os tickets com created_by = Lucas
    tickets_lucas = Ticket.objects.filter(created_by=lucas).order_by('id')
    total_tickets = tickets_lucas.count()
    print(f"Total de chamados associados ao Lucas: {total_tickets}")
    
    # Buscar notificações de "Novo Chamado" para encontrar o criador real
    # O título segue o padrão "Novo Chamado #ID"
    
    correcoes = []
    sem_notificacao = []
    ja_corretos = []
    
    print("\n" + "-" * 80)
    print("PROCESSANDO CHAMADOS...")
    print("-" * 80)
    
    for ticket in tickets_lucas:
        # Buscar notificação de criação do chamado
        # O título é "Novo Chamado #ID"
        notif = PushNotification.objects.filter(
            notification_type='TICKET',
            title__icontains=f'Novo Chamado #{ticket.id}',
        ).exclude(
            title__icontains='comentário'
        ).exclude(
            title__icontains='Atribuído'
        ).first()
        
        # Também tentar buscar pelo extra_data
        if not notif:
            notif = PushNotification.objects.filter(
                notification_type='TICKET',
                extra_data__ticket_id=ticket.id,
                title__istartswith='Novo Chamado'
            ).first()
        
        if notif and notif.created_by_id:
            if notif.created_by_id == lucas.id:
                ja_corretos.append({
                    'ticket_id': ticket.id,
                    'title': ticket.title[:50],
                    'notif_id': notif.id,
                    'created_by': notif.created_by.get_full_name() if notif.created_by else 'N/A'
                })
            else:
                correcoes.append({
                    'ticket': ticket,
                    'ticket_id': ticket.id,
                    'title': ticket.title[:50],
                    'old_created_by_id': ticket.created_by_id,
                    'old_created_by_name': ticket.created_by.get_full_name() if ticket.created_by else 'N/A',
                    'new_created_by_id': notif.created_by_id,
                    'new_created_by_name': notif.created_by.get_full_name() if notif.created_by else 'N/A',
                    'notif_id': notif.id
                })
        else:
            sem_notificacao.append({
                'ticket_id': ticket.id,
                'title': ticket.title[:50],
                'created_at': ticket.created_at
            })
    
    # Mostrar resultados
    print(f"\n{'=' * 80}")
    print("RESUMO DA ANÁLISE")
    print(f"{'=' * 80}")
    print(f"Total de chamados analisados: {total_tickets}")
    print(f"Chamados a corrigir: {len(correcoes)}")
    print(f"Chamados já corretos (Lucas é realmente o criador): {len(ja_corretos)}")
    print(f"Chamados sem notificação encontrada: {len(sem_notificacao)}")
    
    # Mostrar correções a serem feitas
    if correcoes:
        print(f"\n{'-' * 80}")
        print("CORREÇÕES A SEREM APLICADAS:")
        print(f"{'-' * 80}")
        
        # Agrupar por novo criador
        por_criador = {}
        for c in correcoes:
            key = c['new_created_by_name']
            if key not in por_criador:
                por_criador[key] = []
            por_criador[key].append(c)
        
        print(f"\nTotal de {len(por_criador)} criadores diferentes encontrados:")
        for criador, tickets in sorted(por_criador.items(), key=lambda x: -len(x[1])):
            print(f"  {criador}: {len(tickets)} chamados")
        
        print(f"\nDetalhes das primeiras 20 correções:")
        for i, c in enumerate(correcoes[:20], 1):
            print(f"  {i}. Ticket #{c['ticket_id']}: {c['title']}")
            print(f"     De: {c['old_created_by_name']} (ID {c['old_created_by_id']}) -> Para: {c['new_created_by_name']} (ID {c['new_created_by_id']})")
    
    # Mostrar tickets sem notificação
    if sem_notificacao:
        print(f"\n{'-' * 80}")
        print("CHAMADOS SEM NOTIFICAÇÃO (não será possível corrigir automaticamente):")
        print(f"{'-' * 80}")
        for i, t in enumerate(sem_notificacao[:20], 1):
            print(f"  {i}. Ticket #{t['ticket_id']}: {t['title']} - Criado em: {t['created_at']}")
        if len(sem_notificacao) > 20:
            print(f"  ... e mais {len(sem_notificacao) - 20} chamados")
    
    # Aplicar correções se não for dry_run
    if not dry_run and correcoes:
        print(f"\n{'=' * 80}")
        print("APLICANDO CORREÇÕES...")
        print(f"{'=' * 80}")
        
        with transaction.atomic():
            updated = 0
            for c in correcoes:
                ticket = c['ticket']
                ticket.created_by_id = c['new_created_by_id']
                ticket.save(update_fields=['created_by_id'])
                updated += 1
                
                if updated % 100 == 0:
                    print(f"  {updated} chamados corrigidos...")
        
        print(f"\n✅ {updated} chamados corrigidos com sucesso!")
    elif dry_run and correcoes:
        print(f"\n⚠️  MODO DRY RUN - Nenhuma alteração foi feita!")
        print(f"    Execute com dry_run=False para aplicar as {len(correcoes)} correções.")
    
    return {
        'correcoes': correcoes,
        'sem_notificacao': sem_notificacao,
        'ja_corretos': ja_corretos
    }


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Corrige o created_by dos chamados restaurados')
    parser.add_argument('--apply', action='store_true', help='Aplica as correções (sem esta flag, apenas mostra o que seria feito)')
    parser.add_argument('--lucas-id', type=int, default=3, help='ID do usuário Lucas (padrão: 3)')
    
    args = parser.parse_args()
    
    dry_run = not args.apply
    
    if not dry_run:
        print("\n⚠️  ATENÇÃO: Você está prestes a modificar dados no banco!")
        confirm = input("Digite 'SIM' para confirmar: ")
        if confirm != 'SIM':
            print("Operação cancelada.")
            return
    
    analyze_tickets_to_fix(lucas_id=args.lucas_id, dry_run=dry_run)


if __name__ == '__main__':
    main()
