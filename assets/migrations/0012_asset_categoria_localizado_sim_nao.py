from django.db import migrations, models


def padronizar_localizado(apps, schema_editor):
    """'sim', 'Nao', 'NAO'... viram SIM/NÃO. O que não for nenhum dos dois fica como está
    (a tela de edição pede para escolher)."""
    import unicodedata
    Asset = apps.get_model('assets', 'Asset')
    for asset in Asset.objects.exclude(localizado__in=['SIM', 'NÃO']).only('pk', 'localizado'):
        texto = unicodedata.normalize('NFKD', asset.localizado or '').encode('ascii', 'ignore').decode().strip().upper()
        novo = {'SIM': 'SIM', 'S': 'SIM', 'NAO': 'NÃO', 'N': 'NÃO'}.get(texto)
        if novo:
            Asset.objects.filter(pk=asset.pk).update(localizado=novo)


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0011_pdv_com_nome_do_setor'),
    ]

    operations = [
        # Os que já existem entram como "Não Localizado".
        migrations.AddField(
            model_name='asset',
            name='categoria',
            field=models.CharField(choices=[('eletronico', 'Eletrônico'), ('movel', 'Móvel'), ('utilitarios', 'Utilitários'), ('nao_localizado', 'Não Localizado')], default='nao_localizado', max_length=20, verbose_name='Categoria'),
        ),
        migrations.AlterField(
            model_name='asset',
            name='localizado',
            field=models.CharField(choices=[('SIM', 'SIM'), ('NÃO', 'NÃO')], max_length=200, verbose_name='Localizado'),
        ),
        migrations.RunPython(padronizar_localizado, migrations.RunPython.noop),
    ]
