from django.db import migrations, models
import knowledge_trails.models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge_trails', '0006_alter_lesson_lesson_type_slideimage'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgetrail',
            name='icon_emoji',
            field=models.CharField(
                default='📚',
                help_text='Emoji exibido quando não houver imagem de logo',
                max_length=10,
                verbose_name='Emoji da Trilha',
            ),
        ),
        migrations.AddField(
            model_name='trailmodule',
            name='logo_image',
            field=models.ImageField(
                blank=True,
                help_text='Imagem exibida no lugar do emoji quando enviada',
                null=True,
                upload_to=knowledge_trails.models.upload_module_logo,
                verbose_name='Logo do Módulo',
            ),
        ),
        migrations.AddField(
            model_name='lesson',
            name='icon_emoji',
            field=models.CharField(
                default='📖',
                help_text='Emoji exibido quando não houver imagem de logo',
                max_length=10,
                verbose_name='Emoji da Lição',
            ),
        ),
        migrations.AddField(
            model_name='lesson',
            name='logo_image',
            field=models.ImageField(
                blank=True,
                help_text='Imagem exibida no lugar do emoji quando enviada',
                null=True,
                upload_to=knowledge_trails.models.upload_lesson_logo,
                verbose_name='Logo da Lição',
            ),
        ),
    ]
