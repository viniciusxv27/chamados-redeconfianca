from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agenda', '0005_transcription_advanced_fields'),
        ('core', '0023_make_due_date_nullable'),
    ]

    operations = [
        migrations.AddField(
            model_name='meetingtranscription',
            name='tasks_created',
            field=models.ManyToManyField(
                blank=True,
                related_name='source_transcription',
                to='core.taskactivity',
                verbose_name='Tarefas Criadas',
            ),
        ),
    ]
