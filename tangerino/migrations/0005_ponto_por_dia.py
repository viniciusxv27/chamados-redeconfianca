"""Reformata o espelho de ponto: uma linha por pessoa/dia, em colunas.

Antes era uma linha por par entrada/saída, do jeito que a API entrega. Agora é
o formato de cartão de ponto — nome, data, entrada1, saída1, entrada2, saída2
(mais um terceiro par, porque 0,7% dos dias reais têm três).

A tabela é um **espelho** do Tangerino, não a fonte da verdade: por isso ela é
recriada em vez de convertida linha a linha. Nada se perde — os dados voltam na
primeira sincronização, que é disparada logo após a migration.
"""
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('tangerino', '0004_registropontoportal_foto_url_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(name='MarcacaoPonto'),
        migrations.CreateModel(
            name='MarcacaoPonto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('employee_id', models.IntegerField(db_index=True,
                                                    verbose_name='ID do funcionário')),
                ('nome', models.CharField(blank=True, db_index=True, max_length=200,
                                          verbose_name='Nome')),
                ('data', models.DateField(db_index=True, verbose_name='Data')),
                ('entrada1', models.DateTimeField(blank=True, null=True, verbose_name='Entrada 1')),
                ('saida1', models.DateTimeField(blank=True, null=True, verbose_name='Saída 1')),
                ('entrada2', models.DateTimeField(blank=True, null=True, verbose_name='Entrada 2')),
                ('saida2', models.DateTimeField(blank=True, null=True, verbose_name='Saída 2')),
                ('entrada3', models.DateTimeField(blank=True, null=True, verbose_name='Entrada 3')),
                ('saida3', models.DateTimeField(blank=True, null=True, verbose_name='Saída 3')),
                ('marcacoes_extras', models.JSONField(
                    blank=True, default=list,
                    help_text='Rede de segurança: nada é descartado se o dia tiver mais pares.',
                    verbose_name='Marcações além do 3º par')),
                ('total_segundos', models.PositiveIntegerField(
                    default=0, verbose_name='Trabalhado (segundos)')),
                ('em_aberto', models.BooleanField(
                    default=False, verbose_name='Tem entrada sem saída')),
                ('plataforma', models.CharField(blank=True, max_length=30,
                                                verbose_name='Plataforma')),
                ('editado', models.BooleanField(default=False,
                                                verbose_name='Editado no Tangerino')),
                ('tangerino_ids', models.JSONField(
                    blank=True, default=list, verbose_name='IDs dos pares no Tangerino')),
                ('sincronizado_em', models.DateTimeField(verbose_name='Sincronizado em')),
                ('usuario', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='marcacoes_ponto', to=settings.AUTH_USER_MODEL,
                    verbose_name='Usuário do portal')),
            ],
            options={
                'verbose_name': 'Ponto do dia (sincronizado)',
                'verbose_name_plural': 'Pontos do dia (sincronizados)',
                'ordering': ['-data', 'nome'],
            },
        ),
        migrations.AddConstraint(
            model_name='marcacaoponto',
            constraint=models.UniqueConstraint(fields=('employee_id', 'data'),
                                               name='tangerino_ponto_unico_por_dia'),
        ),
        migrations.AddIndex(
            model_name='marcacaoponto',
            index=models.Index(fields=['data', 'nome'], name='tangerino_m_data_f779f2_idx'),
        ),
    ]
