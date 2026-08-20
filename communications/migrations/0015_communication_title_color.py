import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0014_popup_comunicados_pendentes'),
    ]

    operations = [
        migrations.AddField(
            model_name='communication',
            name='title_color',
            field=models.CharField(
                blank=True, default='', max_length=7,
                validators=[django.core.validators.RegexValidator(
                    '^#[0-9A-Fa-f]{6}$', 'Use uma cor no formato #RRGGBB.')],
                verbose_name='Cor do título'),
        ),
    ]
