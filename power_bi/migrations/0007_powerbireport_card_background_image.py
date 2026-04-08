from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('power_bi', '0006_goalupload_fixa_as_percentage'),
    ]

    operations = [
        migrations.AddField(
            model_name='powerbireport',
            name='card_background_image',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to='power_bi/backgrounds/',
                verbose_name='Imagem de fundo do card',
            ),
        ),
    ]
