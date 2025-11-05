"""
Script de debug detalhado para upload de arquivos
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings
from checklists.models import ChecklistTemplate, ChecklistTask

print("=" * 80)
print("DEBUG DETALHADO - UPLOAD DE ARQUIVOS")
print("=" * 80)

print("\n1️⃣ CONFIGURAÇÕES:")
print(f"USE_S3: {getattr(settings, 'USE_S3', False)}")
print(f"MEDIA_ROOT: {settings.MEDIA_ROOT}")
print(f"MEDIA_URL: {settings.MEDIA_URL}")

print("\n2️⃣ VERIFICANDO FUNÇÃO get_media_storage():")
from checklists.models import get_media_storage
storage = get_media_storage()
print(f"get_media_storage() retorna: {storage}")
print(f"Tipo: {type(storage)}")

print("\n3️⃣ VERIFICANDO CAMPOS DO MODELO:")
from django.db.models import FileField, ImageField

task_model = ChecklistTask
for field in task_model._meta.get_fields():
    if isinstance(field, (FileField, ImageField)):
        print(f"\n{field.name}:")
        print(f"  - upload_to: {field.upload_to}")
        print(f"  - storage class: {field.storage.__class__.__name__}")
        print(f"  - storage: {field.storage}")

print("\n4️⃣ ÚLTIMOS TEMPLATES CRIADOS (últimos 5):")
templates = ChecklistTemplate.objects.all().order_by('-created_at')[:5]
for t in templates:
    print(f"\n📝 Template: {t.name} (ID: {t.id})")
    print(f"   Criado em: {t.created_at}")
    print(f"   Número de tarefas: {t.tasks.count()}")
    
    for task in t.tasks.all():
        print(f"\n   ➜ Tarefa: {task.title}")
        print(f"      instruction_image: {task.instruction_image.name if task.instruction_image else 'VAZIO'}")
        print(f"      instruction_video: {task.instruction_video.name if task.instruction_video else 'VAZIO'}")
        print(f"      instruction_document: {task.instruction_document.name if task.instruction_document else 'VAZIO'}")
        
        # Verificar se os arquivos existem fisicamente
        if task.instruction_image:
            path = task.instruction_image.path if hasattr(task.instruction_image, 'path') else 'N/A'
            exists = os.path.exists(path) if path != 'N/A' else False
            print(f"      📷 Imagem existe no disco: {exists} ({path})")
        
        if task.instruction_video:
            path = task.instruction_video.path if hasattr(task.instruction_video, 'path') else 'N/A'
            exists = os.path.exists(path) if path != 'N/A' else False
            print(f"      🎥 Vídeo existe no disco: {exists} ({path})")
        
        if task.instruction_document:
            path = task.instruction_document.path if hasattr(task.instruction_document, 'path') else 'N/A'
            exists = os.path.exists(path) if path != 'N/A' else False
            print(f"      📄 Documento existe no disco: {exists} ({path})")

print("\n5️⃣ TESTANDO CRIAÇÃO DE TAREFA COM ARQUIVO:")
print("Simulando criação de tarefa...")

# Criar um template de teste
test_template = ChecklistTemplate.objects.filter(name='TESTE DEBUG UPLOAD').first()
if not test_template:
    from users.models import Sector, User
    sector = Sector.objects.first()
    user = User.objects.filter(is_superuser=True).first()
    if sector and user:
        test_template = ChecklistTemplate.objects.create(
            name='TESTE DEBUG UPLOAD',
            description='Template de teste para debug',
            sector=sector,
            created_by=user
        )
        print(f"✅ Template de teste criado: ID {test_template.id}")
    else:
        print("❌ Não foi possível criar template de teste")
        test_template = None

if test_template:
    # Criar tarefa sem arquivo
    task = ChecklistTask.objects.create(
        template=test_template,
        title='Tarefa de teste',
        description='Descrição de teste',
        order=0
    )
    print(f"✅ Tarefa criada: ID {task.id}")
    print(f"   instruction_image antes: {task.instruction_image}")
    
    # Tentar criar um arquivo fake para teste
    from django.core.files.uploadedfile import SimpleUploadedFile
    import io
    
    # Criar uma imagem PNG simples (1x1 pixel vermelho)
    fake_image = SimpleUploadedFile(
        name='test_image.png',
        content=b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x00\x00\x00\x00IEND\xaeB`\x82',
        content_type='image/png'
    )
    
    print(f"   Arquivo fake criado: {fake_image.name}, size: {fake_image.size}")
    
    # Tentar atribuir e salvar
    task.instruction_image = fake_image
    task.save()
    
    print(f"   instruction_image depois: {task.instruction_image}")
    print(f"   instruction_image.name: {task.instruction_image.name if task.instruction_image else 'VAZIO'}")
    print(f"   instruction_image.url: {task.instruction_image.url if task.instruction_image else 'VAZIO'}")
    
    if task.instruction_image:
        print("   ✅ ARQUIVO SALVO COM SUCESSO!")
    else:
        print("   ❌ ARQUIVO NÃO FOI SALVO!")

print("\n" + "=" * 80)
print("FIM DO DEBUG")
print("=" * 80)
