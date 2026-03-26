from django.conf import settings
from django.db import migrations, models

import core.storage


class Migration(migrations.Migration):

    dependencies = [
        ('contestacao', '0007_exclusionrecord_gerente_exclusionrecord_imei'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ContestationCartDraft',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.TextField(blank=True, default='', verbose_name='Motivo')),
                ('attachment', models.FileField(blank=True, null=True, storage=core.storage.get_media_storage(), upload_to='contestacoes/rascunhos/%Y/%m/', verbose_name='Anexo de Rascunho')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Criado em')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Atualizado em')),
                ('exclusion', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='contestation_cart_drafts', to='contestacao.exclusionrecord', verbose_name='Registro de Exclusao')),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='contestation_cart_drafts', to=settings.AUTH_USER_MODEL, verbose_name='Usuario')),
            ],
            options={
                'verbose_name': 'Rascunho de Carrinho de Contestacao',
                'verbose_name_plural': 'Rascunhos de Carrinho de Contestacao',
                'ordering': ['-updated_at'],
                'indexes': [models.Index(fields=['user', 'updated_at'], name='contestacao__user_id_6dbd40_idx')],
                'constraints': [models.UniqueConstraint(fields=('user', 'exclusion'), name='uniq_contestation_cart_draft_user_exclusion')],
            },
        ),
    ]
