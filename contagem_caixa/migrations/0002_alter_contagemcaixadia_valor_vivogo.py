from django.db import migrations, models


def zero_vira_vazio(apps, schema_editor):
    """Antes o campo não aceitava vazio, então 'não contei' era gravado como 0.

    Com o campo aceitando nulo, o zero volta a significar 'contei e deu zero'.
    Os registros antigos, que só podiam ser zero por falta de contagem, viram
    vazio — senão todo dia importado apareceria como divergência e alertaria o
    gerente sem ninguém ter contado nada.
    """
    Dia = apps.get_model('contagem_caixa', 'ContagemCaixaDia')
    Dia.objects.filter(valor_vivogo=0).update(valor_vivogo=None)


def vazio_vira_zero(apps, schema_editor):
    Dia = apps.get_model('contagem_caixa', 'ContagemCaixaDia')
    Dia.objects.filter(valor_vivogo__isnull=True).update(valor_vivogo=0)


class Migration(migrations.Migration):

    dependencies = [
        ('contagem_caixa', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contagemcaixadia',
            name='valor_vivogo',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12,
                                      null=True, verbose_name='Vivo go EA'),
        ),
        migrations.RunPython(zero_vira_vazio, vazio_vira_zero),
    ]
