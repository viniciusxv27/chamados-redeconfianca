from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('validad1', '0003_alter_vendad1chatmessage_texto_vendad1chatattachment'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendad1',
            name='penalidade_detalhe',
            field=models.TextField(blank=True, verbose_name='Detalhamento da penalidade'),
        ),
    ]
