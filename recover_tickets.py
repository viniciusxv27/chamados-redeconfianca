#!/usr/bin/env python
"""
Script para recuperar tickets a partir das notificações
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from notifications.models import PushNotification
from tickets.models import Ticket, Category
from users.models import User, Sector
from django.db.models import Q
from django.utils import timezone
import re
from collections import defaultdict
from datetime import datetime

def extract_ticket_data():
    """Extrai dados dos tickets das notificações"""
    
    # Estrutura para armazenar dados de cada ticket
    tickets_data = defaultdict(lambda: {
        'id': None,
        'title': None,
        'description': None,
        'sector_name': None,
        'priority': None,
        'status': None,
        'created_at': None,
        'assigned_to_name': None,
        'created_by_name': None,
        'notifications': []
    })
    
    # Procurar em todas as notificações relacionadas a tickets
    notifs = PushNotification.objects.filter(
        Q(notification_type='TICKET') | 
        Q(action_url__icontains='/tickets/')
    ).order_by('created_at')
    
    print(f'Analisando {notifs.count()} notificações...')
    print()
    
    for n in notifs:
        # Extrair ID do ticket da URL
        ticket_id = None
        if n.action_url:
            match = re.search(r'/tickets/(\d+)/', n.action_url)
            if match:
                ticket_id = int(match.group(1))
        
        # Extrair ID do titulo se não encontrou na URL
        if not ticket_id and n.title:
            match = re.search(r'#(\d+)', n.title)
            if match:
                ticket_id = int(match.group(1))
        
        if not ticket_id:
            continue
        
        data = tickets_data[ticket_id]
        data['id'] = ticket_id
        data['notifications'].append({
            'title': n.title,
            'message': n.message,
            'created_at': n.created_at
        })
        
        # PADRÃO 1: 'Novo Chamado #XXX'
        # Mensagem: TITULO\nSetor: SETOR\nPrioridade: PRIORIDADE
        if n.title and 'Novo Chamado #' in n.title and re.match(r'^Novo Chamado #\d+$', n.title.strip()):
            lines = n.message.split('\n') if n.message else []
            if lines:
                if not data['title']:
                    data['title'] = lines[0].strip()
                if not data['created_at']:
                    data['created_at'] = n.created_at
            
            # Extrair setor e prioridade da mensagem
            if n.message:
                setor_match = re.search(r'Setor:\s*(.+?)(?:\n|$)', n.message)
                if setor_match and not data['sector_name']:
                    data['sector_name'] = setor_match.group(1).strip()
                
                prio_match = re.search(r'Prioridade:\s*(.+?)(?:\n|$)', n.message)
                if prio_match and not data['priority']:
                    data['priority'] = prio_match.group(1).strip()
        
        # PADRÃO 2: 'Novo Chamado: TITULO'
        elif n.title and n.title.startswith('Novo Chamado: '):
            if not data['title']:
                data['title'] = n.title.replace('Novo Chamado: ', '').strip()
            if not data['created_at']:
                data['created_at'] = n.created_at
        
        # PADRÃO 3: '🎫 Novo Chamado de Teste #XXX'
        elif n.title and 'Novo Chamado de Teste' in n.title:
            if not data['title']:
                # O título pode estar na mensagem
                if n.message:
                    lines = n.message.split('\n')
                    if lines:
                        data['title'] = lines[0].strip()
            if not data['created_at']:
                data['created_at'] = n.created_at
        
        # PADRÃO 4: '🎫 Chamado Atribuído: #XXX'
        # Mensagem: "Você foi atribuído ao chamado 'TITULO' por USUARIO."
        if n.title and 'Chamado Atribuído' in n.title:
            if n.message:
                title_match = re.search(r"'(.+?)'", n.message)
                if title_match and not data['title']:
                    data['title'] = title_match.group(1).strip()
                
                by_match = re.search(r' por (.+?)\.', n.message)
                if by_match:
                    data['assigned_by_name'] = by_match.group(1).strip()
        
        # PADRÃO 5: 'Chamado #XXX - STATUS'
        # Mensagem: "O chamado 'TITULO' teve seu status alterado para STATUS."
        if n.title:
            status_match = re.match(r'Chamado #\d+ - (.+)$', n.title)
            if status_match:
                data['status'] = status_match.group(1).strip()
                
                if n.message:
                    title_match = re.search(r"'(.+?)'", n.message)
                    if title_match and not data['title']:
                        data['title'] = title_match.group(1).strip()
        
        # PADRÃO 6: 'Novo comentário no Chamado #XXX'
        if n.title and 'Novo comentário' in n.title:
            # Não tem título do ticket neste padrão
            pass
    
    return tickets_data


def analyze_data(tickets_data):
    """Analisa os dados extraídos"""
    print(f'Total de tickets identificados: {len(tickets_data)}')
    print()
    
    # Contar quantos têm cada campo
    with_title = sum(1 for d in tickets_data.values() if d['title'])
    with_sector = sum(1 for d in tickets_data.values() if d['sector_name'])
    with_priority = sum(1 for d in tickets_data.values() if d['priority'])
    with_created_at = sum(1 for d in tickets_data.values() if d['created_at'])
    
    print(f'Com título: {with_title}')
    print(f'Com setor: {with_sector}')
    print(f'Com prioridade: {with_priority}')
    print(f'Com data de criação: {with_created_at}')
    print()
    
    # Listar setores encontrados
    sectors = set()
    for d in tickets_data.values():
        if d['sector_name']:
            sectors.add(d['sector_name'])
    print(f'Setores encontrados: {sorted(sectors)}')
    print()
    
    # Listar prioridades encontradas
    priorities = set()
    for d in tickets_data.values():
        if d['priority']:
            priorities.add(d['priority'])
    print(f'Prioridades encontradas: {sorted(priorities)}')
    print()
    
    # Mostrar alguns exemplos
    print('=== EXEMPLOS DE DADOS RECUPERADOS ===')
    for tid in sorted(tickets_data.keys())[:15]:
        d = tickets_data[tid]
        print(f'ID: {tid}')
        print(f'  Título: {d["title"]}')
        print(f'  Setor: {d["sector_name"]}')
        print(f'  Prioridade: {d["priority"]}')
        print(f'  Status: {d["status"]}')
        print(f'  Criado em: {d["created_at"]}')
        print()


def map_priority(priority_str):
    """Mapeia a string de prioridade para o valor do banco"""
    if not priority_str:
        return 'MEDIA'
    
    priority_lower = priority_str.lower()
    if 'baixa' in priority_lower:
        return 'BAIXA'
    elif 'alta' in priority_lower:
        return 'ALTA'
    elif 'crítica' in priority_lower or 'critica' in priority_lower:
        return 'CRITICA'
    else:
        return 'MEDIA'


def map_status(status_str):
    """Mapeia a string de status para o valor do banco"""
    if not status_str:
        return 'ABERTO'
    
    status_lower = status_str.lower()
    if 'fechado' in status_lower:
        return 'FECHADO'
    elif 'andamento' in status_lower:
        return 'EM_ANDAMENTO'
    elif 'resolvido' in status_lower:
        return 'RESOLVIDO'
    elif 'aguardando' in status_lower:
        return 'AGUARDANDO_APROVACAO'
    elif 'reaberto' in status_lower:
        return 'REABERTO'
    elif 'rejeitado' in status_lower:
        return 'REJEITADO'
    else:
        return 'ABERTO'


def find_sector(sector_name):
    """Encontra o setor pelo nome"""
    if not sector_name:
        return None
    
    try:
        return Sector.objects.get(name__iexact=sector_name)
    except Sector.DoesNotExist:
        # Tentar busca parcial
        sectors = Sector.objects.filter(name__icontains=sector_name)
        if sectors.exists():
            return sectors.first()
        return None


def recover_tickets(tickets_data, dry_run=True):
    """Recupera os tickets no banco de dados"""
    from django.db import connection
    
    # Pegar um usuário admin para ser o criador padrão
    try:
        default_user = User.objects.filter(is_superuser=True).first()
        if not default_user:
            default_user = User.objects.first()
    except:
        print("ERRO: Não foi possível encontrar um usuário padrão")
        return
    
    # Pegar um setor padrão
    default_sector = Sector.objects.first()
    if not default_sector:
        print("ERRO: Não existe nenhum setor no banco")
        return
    
    print(f'\nUsuário padrão: {default_user}')
    print(f'Setor padrão: {default_sector}')
    print()
    
    # Tickets já existentes
    existing_ids = set(Ticket.objects.values_list('id', flat=True))
    print(f'Tickets já existentes: {len(existing_ids)}')
    
    # Contar quantos serão criados
    to_create = []
    for tid, data in sorted(tickets_data.items()):
        if tid in existing_ids:
            continue
        
        # Deve ter pelo menos um título
        if not data['title']:
            continue
        
        to_create.append((tid, data))
    
    print(f'Tickets a serem criados: {len(to_create)}')
    print()
    
    if dry_run:
        print("=== MODO DRY RUN - Nenhum ticket será criado ===")
        print()
        for tid, data in to_create[:20]:
            sector = find_sector(data['sector_name']) or default_sector
            print(f"ID: {tid}")
            print(f"  Título: {data['title']}")
            print(f"  Setor: {sector.name}")
            print(f"  Prioridade: {map_priority(data['priority'])}")
            print(f"  Status: {map_status(data['status'])}")
            print(f"  Criado em: {data['created_at']}")
            print()
        
        if len(to_create) > 20:
            print(f"... e mais {len(to_create) - 20} tickets")
        
        return to_create
    
    # Criar tickets usando SQL direto para controlar o ID e created_at
    created_count = 0
    errors = []
    
    for tid, data in to_create:
        try:
            sector = find_sector(data['sector_name']) or default_sector
            title = data['title'][:200] if data['title'] else 'Sem título'
            description = title
            status = map_status(data['status'])
            priority = map_priority(data['priority'])
            created_at = data['created_at'] or timezone.now()
            
            # Usar SQL direto para inserir com ID específico
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO tickets_ticket 
                    (id, title, description, sector_id, status, priority, created_by_id, 
                     created_at, updated_at, requires_approval, is_anonymous, solution_time_hours, solution)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, [
                    tid,
                    title,
                    description,
                    sector.id,
                    status,
                    priority,
                    default_user.id,
                    created_at,
                    timezone.now(),
                    False,
                    False,
                    24,
                    ''  # solution vazio
                ])
            
            created_count += 1
            
            if created_count % 100 == 0:
                print(f"Criados: {created_count}/{len(to_create)}")
                
        except Exception as e:
            errors.append((tid, str(e)))
    
    # Atualizar a sequência do ID auto-increment
    try:
        max_id = max(tid for tid, _ in to_create)
        with connection.cursor() as cursor:
            # Para PostgreSQL
            cursor.execute(f"SELECT setval('tickets_ticket_id_seq', {max_id + 1}, false)")
    except Exception as e:
        print(f"Aviso: Não foi possível atualizar a sequência: {e}")
    
    print()
    print(f"=== RESULTADO ===")
    print(f"Tickets criados: {created_count}")
    print(f"Erros: {len(errors)}")
    
    if errors:
        print("\nPrimeiros 10 erros:")
        for tid, err in errors[:10]:
            print(f"  ID {tid}: {err}")
    
    return created_count


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Recuperar tickets das notificações')
    parser.add_argument('--execute', action='store_true', help='Executar a recuperação (sem isso é apenas análise)')
    args = parser.parse_args()
    
    print("=" * 60)
    print("RECUPERAÇÃO DE TICKETS A PARTIR DAS NOTIFICAÇÕES")
    print("=" * 60)
    print()
    
    # Extrair dados
    tickets_data = extract_ticket_data()
    
    # Analisar
    analyze_data(tickets_data)
    
    # Recuperar
    if args.execute:
        print("\n" + "=" * 60)
        print("EXECUTANDO RECUPERAÇÃO...")
        print("=" * 60)
        recover_tickets(tickets_data, dry_run=False)
    else:
        print("\n" + "=" * 60)
        print("SIMULAÇÃO (DRY RUN)")
        print("=" * 60)
        recover_tickets(tickets_data, dry_run=True)
        print()
        print("Para executar a recuperação real, rode:")
        print("  python recover_tickets.py --execute")
