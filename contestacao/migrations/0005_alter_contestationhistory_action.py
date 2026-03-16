from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contestacao', '0004_contestation_attachment_wrong_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contestationhistory',
            name='action',
            field=models.CharField(
                choices=[
                    ('created', 'Contestação Criada'),
                    ('approved', 'Aprovada pelo Gestor'),
                    ('approved_and_contested', 'Aprovada e Contestada pelo Gestor'),
                    ('rejected', 'Rejeitada pelo Gestor'),
                    ('confirmed', 'Confirmada pelo Gerente'),
                    ('denied', 'Negada pelo Gerente'),
                    ('paid', 'Marcada como Paga'),
                    ('synced', 'Planilha Sincronizada'),
                ],
                max_length=30,
                verbose_name='Ação',
            ),
        ),
    ]
