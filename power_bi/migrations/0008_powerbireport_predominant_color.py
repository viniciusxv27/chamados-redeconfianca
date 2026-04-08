from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('power_bi', '0007_powerbireport_card_background_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='powerbireport',
            name='predominant_color',
            field=models.CharField(default='#f97316', max_length=7, verbose_name='Cor predominante (neon)'),
        ),
    ]
