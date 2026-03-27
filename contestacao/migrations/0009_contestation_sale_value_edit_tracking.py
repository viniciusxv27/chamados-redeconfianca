from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('contestacao', '0008_contestationcartdraft'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='contestation',
            name='sale_value_edited_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Data da edição da venda'),
        ),
        migrations.AddField(
            model_name='contestation',
            name='sale_value_edited_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='contestations_sale_value_edited', to=settings.AUTH_USER_MODEL, verbose_name='Venda editada por'),
        ),
        migrations.AddField(
            model_name='contestation',
            name='sale_value_original',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Valor original da venda'),
        ),
        migrations.AddField(
            model_name='contestation',
            name='sale_value_was_edited',
            field=models.BooleanField(default=False, verbose_name='Venda editada'),
        ),
    ]
