from django.conf import settings
from decimal import Decimal
import django.db.models.deletion
from django.db import migrations, models

import users.models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0019_user_pcn'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommissionMonthlyTotal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveSmallIntegerField(verbose_name='Ano')),
                ('month', models.PositiveSmallIntegerField(verbose_name='Mês')),
                ('person_name', models.CharField(max_length=255, verbose_name='Nome da Pessoa')),
                ('role', models.CharField(choices=[('cn', 'CN'), ('gerente', 'Gerente'), ('coordenador', 'Coordenador'), ('aparte', 'A parte')], default='cn', max_length=20, verbose_name='Papel')),
                ('total_commission', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12, verbose_name='Valor Total do Comissionamento')),
                ('synced_at', models.DateTimeField(auto_now=True, verbose_name='Sincronizado em')),
                ('source_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='monthly_totals', to='users.commissionspreadsheetversion', verbose_name='Versão de Origem')),
                ('synced_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='commission_total_syncs', to=settings.AUTH_USER_MODEL, verbose_name='Sincronizado por')),
            ],
            options={
                'verbose_name': 'Total de Comissionamento por Mês',
                'verbose_name_plural': 'Totais de Comissionamento por Mês',
                'ordering': ['-year', '-month', 'person_name'],
            },
        ),
        migrations.AddConstraint(
            model_name='commissionmonthlytotal',
            constraint=models.UniqueConstraint(fields=('year', 'month', 'person_name', 'role'), name='unique_commission_monthly_total'),
        ),
        migrations.CreateModel(
            name='AParteCommissionConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('base_salary', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Valor base sobre o qual os fatores de cada pilar são aplicados', max_digits=12, verbose_name='Salário Base')),
                ('factors', models.JSONField(blank=True, default=users.models.default_aparte_factors, help_text='Faixas de atingimento da rede por pilar: [min, max, taxa]', verbose_name='Fatores por Pilar')),
                ('is_active', models.BooleanField(default=True, verbose_name='Ativo')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Última atualização')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='aparte_commission_config', to=settings.AUTH_USER_MODEL, verbose_name='Usuário')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='aparte_config_updates', to=settings.AUTH_USER_MODEL, verbose_name='Atualizado por')),
            ],
            options={
                'verbose_name': 'Configuração de Comissionamento A parte',
                'verbose_name_plural': 'Configurações de Comissionamento A parte',
                'ordering': ['user__first_name', 'user__last_name'],
            },
        ),
    ]
