"""Troca o override booleano `force_mensal` por `periodicity_override`.

O booleano só permitia forçar "mensal". Agora o gestor também escolhe "semanal"
(na importação em lote ou na tela de detalhe), então o override virou um campo
com as duas opções. As folhas que já tinham force_mensal=True são convertidas
para 'mensal' antes da coluna antiga sair — nenhum ajuste manual é perdido.
"""
from django.db import migrations, models


def copiar_override(apps, schema_editor):
    FolhaPonto = apps.get_model('folhaponto', 'FolhaPonto')
    FolhaPonto.objects.filter(force_mensal=True).update(periodicity_override='mensal')


def desfazer_override(apps, schema_editor):
    FolhaPonto = apps.get_model('folhaponto', 'FolhaPonto')
    FolhaPonto.objects.filter(periodicity_override='mensal').update(force_mensal=True)


class Migration(migrations.Migration):

    dependencies = [
        ('folhaponto', '0003_folhaponto_force_mensal'),
    ]

    operations = [
        migrations.AddField(
            model_name='folhaponto',
            name='periodicity_override',
            field=models.CharField(
                blank=True, default='', max_length=10,
                choices=[('semanal', 'Semanal (prévia, não assinável)'),
                         ('mensal', 'Mensal (fechamento, assinável)')],
                help_text='Vazio segue a classificação automática. Preenchido, força a '
                          'periodicidade da folha.',
                verbose_name='Periodicidade (manual)',
            ),
        ),
        migrations.RunPython(copiar_override, desfazer_override),
        migrations.RemoveField(model_name='folhaponto', name='force_mensal'),
    ]
