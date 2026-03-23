from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0012_user_demission_date_user_has_experience_window_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemconfig',
            name='contestacao_global_managers',
            field=models.ManyToManyField(
                blank=True,
                help_text='Usuários liberados para gerenciar tudo em /contestacao',
                related_name='contestacao_global_access_configs',
                to='users.user',
                verbose_name='Gestores Globais de Contestação',
            ),
        ),
    ]
