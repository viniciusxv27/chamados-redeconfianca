# Generated migration to remove RESOLVIDO status

from django.db import migrations, models


def convert_resolvido_to_fechado(apps, schema_editor):
    """Convert all RESOLVIDO tickets to FECHADO"""
    SupportChat = apps.get_model('projects', 'SupportChat')
    from django.utils import timezone
    
    # Update all RESOLVIDO to FECHADO
    updated = SupportChat.objects.filter(status='RESOLVIDO').update(
        status='FECHADO',
        closed_at=timezone.now()
    )
    if updated:
        print(f"Converted {updated} tickets from RESOLVIDO to FECHADO")


def reverse_migration(apps, schema_editor):
    """Reverse is a no-op since we can't determine which were originally RESOLVIDO"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0009_add_response_time_fields'),
    ]

    operations = [
        migrations.RunPython(convert_resolvido_to_fechado, reverse_migration),
        migrations.AlterField(
            model_name='supportchat',
            name='status',
            field=models.CharField(
                choices=[
                    ('AGUARDANDO', 'Aguardando na Fila'),
                    ('ABERTO', 'Aberto'),
                    ('EM_ANDAMENTO', 'Em Andamento'),
                    ('FECHADO', 'Fechado'),
                ],
                default='AGUARDANDO',
                max_length=20,
            ),
        ),
    ]
