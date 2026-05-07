from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('users', '0016_commissionspreadsheetversion_contestacao_phase_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='SimulatorFactorSet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('consultor', 'Consultor'), ('gerente', 'Gerente'), ('coordenador', 'Coordenador')], max_length=20, unique=True)),
                ('data', models.JSONField(blank=True, default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='simulator_factor_updates', to=settings.AUTH_USER_MODEL, verbose_name='Atualizado por')),
            ],
            options={
                'verbose_name': 'Fatores do Simulador',
                'verbose_name_plural': 'Fatores do Simulador',
            },
        ),
        migrations.CreateModel(
            name='CoordinatorStoreAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('coordinator', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='coordinator_store_access', to=settings.AUTH_USER_MODEL, verbose_name='Coordenador')),
                ('sectors', models.ManyToManyField(blank=True, related_name='coordinator_access', to='users.sector', verbose_name='Lojas permitidas')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='coordinator_access_updates', to=settings.AUTH_USER_MODEL, verbose_name='Atualizado por')),
            ],
            options={
                'verbose_name': 'Acesso de Lojas do Coordenador',
                'verbose_name_plural': 'Acessos de Lojas dos Coordenadores',
            },
        ),
    ]
