from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contestacao', '0009_contestation_sale_value_edit_tracking'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExclusionSyncBatch',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Criado em')),
                ('record_count', models.PositiveIntegerField(default=0, verbose_name='Registros importados')),
                ('notes', models.TextField(blank=True, default='', verbose_name='Observações')),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='exclusion_sync_batches',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Sincronizado por',
                )),
            ],
            options={
                'verbose_name': 'Lote de Sincronização de Exclusões',
                'verbose_name_plural': 'Lotes de Sincronização de Exclusões',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddField(
            model_name='exclusionrecord',
            name='sync_batch',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='records',
                to='contestacao.exclusionsyncbatch',
                verbose_name='Lote de Sincronização',
            ),
        ),
        migrations.AddIndex(
            model_name='exclusionrecord',
            index=models.Index(fields=['sync_batch'], name='contestacao_sync_ba_idx'),
        ),
    ]
