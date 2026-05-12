from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agenda', '0008_meetingtranscription_participant_roles'),
    ]

    operations = [
        migrations.AddField(
            model_name='meetingtranscription',
            name='risks',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Riscos e bloqueios identificados pela IA na reunião.',
                verbose_name='Riscos identificados',
            ),
        ),
    ]
