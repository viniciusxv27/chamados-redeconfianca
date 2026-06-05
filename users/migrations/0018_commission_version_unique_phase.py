from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0017_commissionspreadsheetversion_status'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='commissionspreadsheetversion',
            name='unique_commission_version_by_month_year',
        ),
        migrations.AddConstraint(
            model_name='commissionspreadsheetversion',
            constraint=models.UniqueConstraint(
                fields=('year', 'month', 'contestacao_phase'),
                name='unique_commission_version_by_month_year_phase',
            ),
        ),
    ]
