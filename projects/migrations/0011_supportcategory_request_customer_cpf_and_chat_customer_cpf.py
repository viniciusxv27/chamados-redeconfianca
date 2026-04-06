from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0010_remove_resolvido_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='supportcategory',
            name='request_customer_cpf',
            field=models.BooleanField(
                default=False,
                help_text='Quando marcado, exige CPF do cliente na abertura do ticket',
                verbose_name='Solicitar CPF',
            ),
        ),
        migrations.AddField(
            model_name='supportchat',
            name='customer_cpf',
            field=models.CharField(
                blank=True,
                help_text='CPF informado na abertura do ticket quando a categoria exigir',
                max_length=11,
                verbose_name='CPF do Cliente',
            ),
        ),
    ]
