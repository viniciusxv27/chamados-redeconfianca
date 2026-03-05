from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('agenda', '0004_meetingtranscription'),
    ]

    operations = [
        migrations.AddField(
            model_name='meetingtranscription',
            name='sections',
            field=models.JSONField(blank=True, default=list, help_text='Lista de seções/tópicos da reunião com título, conteúdo e tempo', verbose_name='Seções da Reunião'),
        ),
        migrations.AddField(
            model_name='meetingtranscription',
            name='key_decisions',
            field=models.JSONField(blank=True, default=list, help_text='Decisões tomadas durante a reunião', verbose_name='Decisões-Chave'),
        ),
        migrations.AddField(
            model_name='meetingtranscription',
            name='participants_identified',
            field=models.JSONField(blank=True, default=list, help_text='Nomes de participantes detectados na conversa', verbose_name='Participantes Identificados'),
        ),
        migrations.AddField(
            model_name='meetingtranscription',
            name='sentiment',
            field=models.CharField(choices=[('positive', 'Positivo'), ('neutral', 'Neutro'), ('negative', 'Negativo'), ('mixed', 'Misto')], default='neutral', max_length=20, verbose_name='Sentimento Geral'),
        ),
        migrations.AddField(
            model_name='meetingtranscription',
            name='meeting_type_detected',
            field=models.CharField(choices=[('standup', 'Daily/Standup'), ('planning', 'Planejamento'), ('review', 'Review/Retrospectiva'), ('brainstorm', 'Brainstorm'), ('oneonone', '1:1'), ('kickoff', 'Kickoff'), ('status', 'Status Update'), ('decision', 'Tomada de Decisão'), ('general', 'Reunião Geral')], default='general', max_length=20, verbose_name='Tipo Detectado'),
        ),
        migrations.AddField(
            model_name='meetingtranscription',
            name='tags',
            field=models.JSONField(blank=True, default=list, verbose_name='Tags/Palavras-chave'),
        ),
        migrations.AddField(
            model_name='meetingtranscription',
            name='calendar_event_created',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transcription_source', to='agenda.calendarevent', verbose_name='Evento criado na agenda'),
        ),
    ]
