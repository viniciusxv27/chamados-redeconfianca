"""Uma entrevista de desligamento por pessoa (28/10/2026).

Reenviar o formulário (clique duplo, ou de novo para subir o vídeo) criava outro
registro, e o relatório contava a mesma saída duas ou três vezes. Daqui em diante
reenviar atualiza a entrevista que existe; esta migração limpa o que já duplicou.

Só apaga cópia **idêntica** (mesmas respostas) da que fica. A que fica é a que tem
vídeo; sem vídeo em nenhuma, a mais recente. Duplicado com respostas diferentes
não é apagado (aparece no relatório para alguém decidir).
"""
from django.db import migrations


def tirar_duplicadas(apps, schema_editor):
    Resposta = apps.get_model('feedback', 'ExitInterviewResponse')
    Gravacao = apps.get_model('feedback', 'ExitInterviewRecording')
    com_video = set(Gravacao.objects.values_list('response_id', flat=True))
    por_pessoa = {}
    for r in Resposta.objects.exclude(user__isnull=True).order_by('submitted_at', 'id'):
        por_pessoa.setdefault((r.survey_key, r.user_id), []).append(r)
    apagar = []
    for respostas in por_pessoa.values():
        if len(respostas) < 2:
            continue
        fica = next((r for r in reversed(respostas) if r.id in com_video), respostas[-1])
        apagar += [r.id for r in respostas if r.id != fica.id and r.id not in com_video and r.answers == fica.answers]
    if apagar:
        Resposta.objects.filter(id__in=apagar).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('feedback', '0014_exit_interview_refusal'),
    ]

    operations = [
        migrations.RunPython(tirar_duplicadas, migrations.RunPython.noop),
    ]
