from datetime import date

from django.db import migrations, models

# A mesma data de `popup_checkers.REGRA_ATIVA_DESDE`, copiada: migração não
# importa código do app, que pode mudar depois e reescrever o passado.
REGRA_ATIVA_DESDE = date(2026, 7, 27)


def marcar_os_que_ja_travavam(apps, schema_editor):
    """Os comunicados que HOJE travam o portal nascem obrigatórios.

    Até esta migração todo comunicado publicado a partir de 27/07/2026 travava
    no popup de "de acordo". Criar o campo desligado para eles liberaria de uma
    vez avisos que alguém mandou esperando confirmação. Então nada muda para os
    que já existem; quem quiser liberar um desmarca na edição. Os anteriores a
    essa data nunca travaram e continuam desmarcados.
    """
    Communication = apps.get_model('communications', 'Communication')
    Communication.objects.filter(
        created_at__date__gte=REGRA_ATIVA_DESDE).update(obrigatorio=True)


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0016_alter_communication_image_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='communication',
            name='obrigatorio',
            field=models.BooleanField(
                default=False,
                help_text='Marcado, o comunicado trava o portal até a pessoa dar o "Estou Ciente".',
                verbose_name='Obrigatoriedade'),
        ),
        migrations.RunPython(marcar_os_que_ja_travavam, migrations.RunPython.noop),
    ]
