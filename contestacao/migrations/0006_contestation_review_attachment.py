import django.db.models.deletion
from django.db import migrations, models
import core.storage


class Migration(migrations.Migration):

    dependencies = [
        ('contestacao', '0005_alter_contestationhistory_action'),
    ]

    operations = [
        migrations.AddField(
            model_name='contestation',
            name='review_attachment',
            field=models.FileField(
                blank=True,
                null=True,
                storage=core.storage.get_media_storage(),
                upload_to='contestacoes/gestor/%Y/%m/',
                verbose_name='Anexo do Gestor',
            ),
        ),
    ]
