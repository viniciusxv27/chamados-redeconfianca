from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0013_systemconfig_contestacao_global_managers'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemconfig',
            name='excel_base_exclusao_url',
            field=models.URLField(
                default='https://1drv.ms/x/c/871ee1819c7e2faa/IQBryBteOg4sS4cBwU1tIgKoATfi6qmYB8eRrIaTpyP8Qhc?e=pye3Sj',
                help_text='URL de compartilhamento do OneDrive para base de exclusão do comissionamento',
                max_length=500,
                verbose_name='Planilha BASE_EXCLUSAO (Comissionamento)',
            ),
        ),
        migrations.AddField(
            model_name='systemconfig',
            name='excel_contestacao_base_exclusao_url',
            field=models.URLField(
                default='https://1drv.ms/x/c/871ee1819c7e2faa/IQBryBteOg4sS4cBwU1tIgKoATfi6qmYB8eRrIaTpyP8Qhc?e=pye3Sj',
                help_text='URL de compartilhamento do OneDrive para base de exclusão da contestação',
                max_length=500,
                verbose_name='Planilha BASE_EXCLUSAO (Contestação)',
            ),
        ),
        migrations.CreateModel(
            name='CommissionSpreadsheetVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveSmallIntegerField(verbose_name='Ano de Referência')),
                ('month', models.PositiveSmallIntegerField(verbose_name='Mês de Referência')),
                ('excel_comissao_url', models.URLField(help_text='URL de compartilhamento do OneDrive para a planilha de comissões', max_length=500, verbose_name='Planilha de Comissionamento')),
                ('excel_vendas_url', models.URLField(help_text='URL de compartilhamento do OneDrive para vendas e metas', max_length=500, verbose_name='Planilha de Vendas e Metas')),
                ('excel_base_pagamento_url', models.URLField(help_text='URL de compartilhamento do OneDrive para base de pagamento', max_length=500, verbose_name='Planilha BASE_PAGAMENTO')),
                ('excel_base_exclusao_url', models.URLField(help_text='URL de compartilhamento do OneDrive para base de exclusão do comissionamento', max_length=500, verbose_name='Planilha BASE_EXCLUSAO (Comissionamento)')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Última atualização')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='commission_version_updates', to='users.user', verbose_name='Atualizado por')),
            ],
            options={
                'verbose_name': 'Versão de Planilha de Comissionamento',
                'verbose_name_plural': 'Versões de Planilhas de Comissionamento',
                'ordering': ['-year', '-month'],
                'constraints': [models.UniqueConstraint(fields=('year', 'month'), name='unique_commission_version_by_month_year')],
            },
        ),
    ]
