from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0014_commission_versioning_and_contestacao_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemconfig',
            name='display_reference_month',
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text='Mês de referência padrão exibido no /users/commission/ quando não houver seleção manual',
                null=True,
                verbose_name='Mês de Exibição do Comissionamento',
            ),
        ),
        migrations.AddField(
            model_name='systemconfig',
            name='display_reference_year',
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text='Ano de referência padrão exibido no /users/commission/ quando não houver seleção manual',
                null=True,
                verbose_name='Ano de Exibição do Comissionamento',
            ),
        ),
        migrations.CreateModel(
            name='CommissionUserReferenceHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveSmallIntegerField(verbose_name='Ano de Referência')),
                ('month', models.PositiveSmallIntegerField(verbose_name='Mês de Referência')),
                ('user_name', models.CharField(max_length=255, verbose_name='Nome do Usuário')),
                ('role', models.CharField(choices=[('cn', 'CN'), ('gerente', 'Gerente'), ('coordenador', 'Coordenador')], max_length=20, verbose_name='Papel')),
                ('sheet_name', models.CharField(max_length=120, verbose_name='Sheet de Origem')),
                ('row_data', models.JSONField(blank=True, default=dict, verbose_name='Dados da Linha')),
                ('captured_at', models.DateTimeField(auto_now_add=True, verbose_name='Capturado em')),
                ('source_version', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_history_entries', to='users.commissionspreadsheetversion', verbose_name='Versão de Origem')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='commission_history_updates', to='users.user', verbose_name='Atualizado por')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='commission_reference_history', to='users.user', verbose_name='Usuário Vinculado')),
            ],
            options={
                'verbose_name': 'Histórico de Comissionamento por Usuário',
                'verbose_name_plural': 'Históricos de Comissionamento por Usuário',
                'ordering': ['-year', '-month', 'user_name'],
                'constraints': [models.UniqueConstraint(fields=('year', 'month', 'sheet_name', 'user_name'), name='unique_commission_user_history_by_reference_and_sheet')],
            },
        ),
    ]
