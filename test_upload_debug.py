"""
Script de debug para verificar upload de arquivos em checklist templates
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings
from django.db import models
from checklists.models import ChecklistTemplate, ChecklistTask

print("=" * 80)
print("DEBUG DE UPLOAD DE ARQUIVOS - CHECKLIST TEMPLATES")
print("=" * 80)

print("\n📋 CONFIGURAÇÕES DO SISTEMA:")
print(f"USE_S3: {getattr(settings, 'USE_S3', False)}")
print(f"MEDIA_ROOT: {settings.MEDIA_ROOT}")
print(f"MEDIA_URL: {settings.MEDIA_URL}")

if hasattr(settings, 'AWS_STORAGE_BUCKET_NAME'):
    print(f"AWS_STORAGE_BUCKET_NAME: {settings.AWS_STORAGE_BUCKET_NAME}")
    print(f"AWS_S3_ENDPOINT_URL: {getattr(settings, 'AWS_S3_ENDPOINT_URL', 'N/A')}")

print("\n📁 VERIFICANDO MODELOS:")
print("\nChecklistTask - Campos de Upload:")

from checklists.models import ChecklistTask
from django.db.models import FileField, ImageField

for field in ChecklistTask._meta.get_fields():
    if isinstance(field, (FileField, ImageField)):
        print(f"\n  Campo: {field.name}")
        print(f"    Tipo: {field.__class__.__name__}")
        print(f"    Upload To: {field.upload_to}")
        print(f"    Storage: {field.storage if hasattr(field, 'storage') else 'Default'}")
        print(f"    Blank: {field.blank}, Null: {field.null}")

print("\n\n🔍 VERIFICANDO TEMPLATES COM TASKS QUE TÊM ARQUIVOS:")
templates_with_files = ChecklistTemplate.objects.filter(
    tasks__instruction_image__isnull=False
) | ChecklistTemplate.objects.filter(
    tasks__instruction_video__isnull=False
) | ChecklistTemplate.objects.filter(
    tasks__instruction_document__isnull=False
)

templates_with_files = templates_with_files.distinct()

if templates_with_files.exists():
    print(f"\nEncontrados {templates_with_files.count()} template(s) com arquivos:")
    for template in templates_with_files:
        print(f"\n  📝 Template: {template.name} (ID: {template.id})")
        tasks = template.tasks.filter(
            models.Q(instruction_image__isnull=False) |
            models.Q(instruction_video__isnull=False) |
            models.Q(instruction_document__isnull=False)
        )
        for task in tasks:
            print(f"    ➜ Tarefa: {task.title}")
            if task.instruction_image:
                print(f"      📷 Imagem: {task.instruction_image.name}")
                print(f"         URL: {task.instruction_image.url}")
            if task.instruction_video:
                print(f"      🎥 Vídeo: {task.instruction_video.name}")
                print(f"         URL: {task.instruction_video.url}")
            if task.instruction_document:
                print(f"      📄 Documento: {task.instruction_document.name}")
                print(f"         URL: {task.instruction_document.url}")
else:
    print("\n❌ Nenhum template com arquivos encontrado.")

print("\n\n" + "=" * 80)
print("💡 DIAGNÓSTICO:")
print("=" * 80)

print("\n✅ CORREÇÕES JÁ IMPLEMENTADAS:")
print("1. Adicionado storage=get_media_storage() nos campos de instrução")
print("2. Verificação de tamanho do arquivo (file.size > 0)")
print("3. Chamada task.save() após atribuir cada arquivo")
print("4. Uso correto de getlist() para arrays")
print("5. Filtro markdown_simple aplicado nas descrições")

print("\n⚠️  SE O UPLOAD AINDA NÃO FUNCIONA:")
print("1. Verifique se USE_S3=True está configurado no settings")
print("2. Verifique se as credenciais do MinIO estão corretas")
print("3. Verifique se o bucket existe no MinIO")
print("4. Teste fazer upload de um arquivo pequeno (< 1MB)")
print("5. Verifique os logs do Django durante o upload")
print("6. Confirme que o formulário tem enctype='multipart/form-data'")

print("\n🧪 TESTE MANUAL:")
print("1. Acesse: /checklists/admin/templates/create/")
print("2. Preencha nome e setor")
print("3. Adicione uma tarefa com título")
print("4. Faça upload de uma imagem pequena na tarefa")
print("5. Salve o template")
print("6. Verifique se o arquivo aparece ao editar o template")
print("7. Execute este script novamente para ver se o arquivo foi salvo\n")
