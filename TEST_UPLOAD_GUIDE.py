"""
GUIA DE TESTE - UPLOAD DE ARQUIVOS EM CHECKLIST TEMPLATES
"""

print("=" * 80)
print("📋 GUIA DE TESTE - UPLOAD DE ARQUIVOS")
print("=" * 80)

print("\n✅ CORREÇÕES APLICADAS:")
print("\n1. MODELO (checklists/models.py):")
print("   - Adicionado storage=get_media_storage() em todos os campos de instrução")
print("   - Arquivos serão salvos em /media/checklists/instructions/")

print("\n2. VIEWS (checklists/views.py):")
print("   - create_template(): Verificação de tamanho + save() após atribuir")
print("   - edit_template(): Verificação de tamanho + save() após atribuir")
print("   - DEBUG LOGS adicionados para rastrear upload")

print("\n3. TEMPLATES:")
print("   - create_template.html: Nomes corretos task_image[], task_video[], task_document[]")
print("   - edit_template.html: CORRIGIDO de task_instruction_*_X para task_image[]")

print("\n" + "=" * 80)
print("🧪 COMO TESTAR:")
print("=" * 80)

print("\n📝 TESTE 1 - CRIAR NOVO TEMPLATE:")
print("1. Acesse: http://127.0.0.1:8000/checklists/admin/templates/create/")
print("2. Preencha:")
print("   - Nome: 'Teste Upload'")
print("   - Setor: Qualquer um")
print("   - Descrição: Opcional")
print("3. Adicione UMA tarefa:")
print("   - Título: 'Tarefa com Arquivos'")
print("   - MARQUE para fazer upload de:")
print("     ✓ Uma imagem (PNG, JPG)")
print("     ✓ Um vídeo (MP4)")
print("     ✓ Um documento (PDF)")
print("4. Clique em 'Criar Template'")
print("5. Verifique no terminal do servidor os logs de DEBUG")
print("6. Acesse Editar Template - os arquivos devem aparecer!")

print("\n✏️ TESTE 2 - EDITAR TEMPLATE:")
print("1. Acesse a lista de templates")
print("2. Clique em 'Editar' em algum template existente")
print("3. Faça upload de um arquivo em uma tarefa")
print("4. Salve")
print("5. Edite novamente - o arquivo deve aparecer")

print("\n🔍 TESTE 3 - VERIFICAR LOGS:")
print("No terminal onde o servidor está rodando, você verá:")
print("   DEBUG - Arquivos recebidos:")
print("     task_images: X arquivos")
print("     Image 0: nome_do_arquivo.png, size: XXXX")
print("   DEBUG - Processando tarefa 0: Título da Tarefa")
print("     Salvando imagem: nome_do_arquivo.png")
print("     Imagem salva: checklists/instructions/images/nome_do_arquivo.png")

print("\n" + "=" * 80)
print("⚠️ IMPORTANTE:")
print("=" * 80)
print("\n1. O servidor DEVE estar rodando para ver os logs de DEBUG")
print("2. Se os arquivos NÃO aparecerem no formulário de edição:")
print("   - Verifique os logs do servidor")
print("   - Execute: python debug_upload_detailed.py")
print("   - Veja se há erros de permissão na pasta /media/")

print("\n3. Os arquivos são salvos em:")
print("   - Imagens: /media/checklists/instructions/images/")
print("   - Vídeos: /media/checklists/instructions/videos/")
print("   - Documentos: /media/checklists/instructions/documents/")

print("\n4. Se USE_S3=True:")
print("   - Os arquivos vão para o MinIO")
print("   - Verifique se as credenciais estão corretas")

print("\n" + "=" * 80)
print("✅ TESTE RÁPIDO DE VALIDAÇÃO:")
print("=" * 80)

import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from checklists.models import ChecklistTemplate

# Verificar último template criado
last_template = ChecklistTemplate.objects.order_by('-created_at').first()
if last_template:
    print(f"\n📝 Último template criado: {last_template.name}")
    print(f"   ID: {last_template.id}")
    print(f"   Criado em: {last_template.created_at}")
    print(f"   Tarefas: {last_template.tasks.count()}")
    
    for task in last_template.tasks.all():
        print(f"\n   ➜ {task.title}")
        if task.instruction_image:
            print(f"      📷 Imagem: {task.instruction_image.name}")
        if task.instruction_video:
            print(f"      🎥 Vídeo: {task.instruction_video.name}")
        if task.instruction_document:
            print(f"      📄 Documento: {task.instruction_document.name}")
        
        if not (task.instruction_image or task.instruction_video or task.instruction_document):
            print(f"      ⚠️ Sem arquivos")

print("\n" + "=" * 80)
print("🎯 PRONTO PARA TESTAR!")
print("=" * 80)
print("\nTente criar um novo template com arquivos e veja os logs!")
print()
