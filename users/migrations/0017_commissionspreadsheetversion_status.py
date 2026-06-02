from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def mark_existing_as_released(apps, schema_editor):
    """Versões já existentes continuam valendo (liberadas) para não quebrar
    o comissionamento atual; apenas novas alterações nascem como rascunho."""
    CommissionSpreadsheetVersion = apps.get_model('users', 'CommissionSpreadsheetVersion')
    CommissionSpreadsheetVersion.objects.update(status='released')


def revert_status(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0016_commissionspreadsheetversion_contestacao_phase_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='commissionspreadsheetversion',
            name='status',
            field=models.CharField(
                choices=[('draft', 'Rascunho'), ('released', 'Liberada')],
                db_index=True,
                default='draft',
                help_text='Rascunho fica visível apenas para superadmins; só vai ao ar quando liberada.',
                max_length=10,
                verbose_name='Situação',
            ),
        ),
        migrations.AddField(
            model_name='commissionspreadsheetversion',
            name='released_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='commission_version_releases',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Liberada por',
            ),
        ),
        migrations.AddField(
            model_name='commissionspreadsheetversion',
            name='released_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Liberada em'),
        ),
        migrations.RunPython(mark_existing_as_released, revert_status),
    ]
