from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agenda', '0007_add_recurrence_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='meetingtranscription',
            name='participant_roles',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Papéis definidos manualmente para melhorar a identificação de falantes.',
                verbose_name='Papéis dos Participantes',
            ),
        ),
    ]
