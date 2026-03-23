from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contestacao', '0006_contestation_review_attachment'),
    ]

    operations = [
        migrations.AddField(
            model_name='exclusionrecord',
            name='gerente',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='Gerente'),
        ),
        migrations.AddField(
            model_name='exclusionrecord',
            name='imei',
            field=models.CharField(blank=True, default='', max_length=120, verbose_name='IMEI'),
        ),
    ]
