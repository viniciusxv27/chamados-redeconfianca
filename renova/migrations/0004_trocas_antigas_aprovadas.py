"""Avaliações feitas antes da aprovação do gerente já tinham chamado aberto na hora: contam como aprovadas."""
from django.db import migrations


def marcar_aprovadas(apps, schema_editor):
    Renova = apps.get_model('renova', 'Renova')
    for renova in Renova.objects.filter(aprovacao='PENDENTE').only('pk', 'criado_em', 'parecer'):
        renova.aprovacao = 'REPROVADA' if renova.parecer == 'NAO_APROVADO' else 'APROVADA'
        renova.aprovacao_em = renova.criado_em
        renova.save(update_fields=['aprovacao', 'aprovacao_em'])


class Migration(migrations.Migration):

    dependencies = [
        ('renova', '0003_configuracaorenova_financeiro_renova_aprovacao_and_more'),
    ]

    operations = [
        migrations.RunPython(marcar_aprovadas, migrations.RunPython.noop),
    ]
