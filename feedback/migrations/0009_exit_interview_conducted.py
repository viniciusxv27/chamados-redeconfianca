# Entrevista de Desligamento conduzida por entrevistador + desligamento de acesso

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feedback', '0008_alter_climatesurveyresponse_options_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='exitinterviewparticipation',
            name='interviewer',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='exit_interviews_conducted', to=settings.AUTH_USER_MODEL, verbose_name='Entrevistador'),
        ),
        migrations.AddField(
            model_name='exitinterviewparticipation',
            name='dismissal_date',
            field=models.DateField(blank=True, null=True, verbose_name='Data de desligamento informada'),
        ),
        migrations.AddField(
            model_name='exitinterviewparticipation',
            name='dismissal_executed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Desligamento de acesso efetuado em'),
        ),
        migrations.AddField(
            model_name='exitinterviewresponse',
            name='interviewer',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='exit_interview_responses_conducted', to=settings.AUTH_USER_MODEL, verbose_name='Entrevistador'),
        ),
        migrations.AlterField(
            model_name='exitinterviewresponse',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='exit_interview_responses_made', to=settings.AUTH_USER_MODEL, verbose_name='Colaborador desligado'),
        ),
    ]
