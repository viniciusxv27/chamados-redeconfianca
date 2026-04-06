from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('users', '0014_commission_versioning_and_contestacao_url'),
    ]

    operations = [
        migrations.CreateModel(
            name='PowerBIReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, verbose_name='Nome')),
                ('description', models.TextField(blank=True, verbose_name='Descricao')),
                ('icon_class', models.CharField(default='fas fa-chart-line', max_length=80, verbose_name='Icone (classe Font Awesome)')),
                ('embed_url', models.URLField(max_length=1000, verbose_name='Link do Power BI (embed)')),
                ('allowed_hierarchies', models.JSONField(blank=True, default=list, verbose_name='Hierarquias permitidas')),
                ('is_active', models.BooleanField(default=True, verbose_name='Ativo')),
                ('sort_order', models.PositiveIntegerField(default=0, verbose_name='Ordem')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('allowed_groups', models.ManyToManyField(blank=True, related_name='power_bi_reports', to='auth.group', verbose_name='Grupos permitidos')),
                ('allowed_sectors', models.ManyToManyField(blank=True, related_name='power_bi_reports', to='users.sector', verbose_name='Setores permitidos')),
                ('allowed_users', models.ManyToManyField(blank=True, related_name='power_bi_reports', to='users.user', verbose_name='Usuarios permitidos')),
            ],
            options={
                'verbose_name': 'Relatorio Power BI',
                'verbose_name_plural': 'Relatorios Power BI',
                'ordering': ['sort_order', 'name'],
            },
        ),
    ]
