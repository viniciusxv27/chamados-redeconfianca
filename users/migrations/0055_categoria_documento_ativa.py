from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0054_categoria_documento_criada'),
    ]

    operations = [
        migrations.AddField(
            model_name='categoriadocumento',
            name='ativa',
            field=models.BooleanField(db_default=True, default=True, verbose_name='Ativa'),
        ),
    ]
