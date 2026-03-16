from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contestacao', '0003_exclusionrecord_observacao_contestationhistory'),
    ]

    operations = [
        migrations.AddField(
            model_name='contestation',
            name='approval_mode',
            field=models.CharField(
                choices=[('approved', 'Aprovar'), ('approved_and_contested', 'Aprovar e Contestar')],
                default='approved',
                max_length=30,
                verbose_name='Modo de Aprovação',
            ),
        ),
        migrations.AddField(
            model_name='contestation',
            name='attachment_wrong',
            field=models.BooleanField(default=False, verbose_name='Anexo veio errado'),
        ),
        migrations.AddField(
            model_name='contestation',
            name='paid_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Data do Pagamento'),
        ),
    ]
