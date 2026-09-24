"""A cobertura do que já está espelhado hoje.

Sem isto, no primeiro dia a tabela de cobertura estaria vazia e o relatório
trataria como "não sei" justamente os 30 dias que a sincronização diária já
trouxe — nenhuma falta apareceria até a primeira busca nova.
"""
from django.db import migrations
from django.db.models import Max, Min


def semear(apps, schema_editor):
    Marcacao = apps.get_model('tangerino', 'MarcacaoPonto')
    Cobertura = apps.get_model('tangerino', 'CoberturaPonto')
    faixas = (Marcacao.objects.values('employee_id')
              .annotate(desde=Min('data'), ate=Max('data')))
    existentes = set(Cobertura.objects.values_list('employee_id', flat=True))
    novos = [Cobertura(employee_id=f['employee_id'], desde=f['desde'], ate=f['ate'])
             for f in faixas if f['employee_id'] not in existentes]
    Cobertura.objects.bulk_create(novos, batch_size=500, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('tangerino', '0016_cobertura_do_ponto'),
    ]

    operations = [
        migrations.RunPython(semear, migrations.RunPython.noop),
    ]
