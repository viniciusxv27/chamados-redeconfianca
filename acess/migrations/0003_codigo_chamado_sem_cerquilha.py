from django.db import migrations


def tirar_cerquilha(apps, schema_editor):
    """O Acess anterior gravava o nº do chamado com "#" ("#564695"); a tela já põe o #."""
    Chamado = apps.get_model('acess', 'Chamado')
    for ch in Chamado.objects.filter(codigo__startswith='#').only('id', 'codigo'):
        limpo = ch.codigo.strip().lstrip('#').strip()
        if limpo:
            Chamado.objects.filter(pk=ch.pk).update(codigo=limpo)


class Migration(migrations.Migration):

    dependencies = [
        ('acess', '0002_importacao_legado'),
    ]

    operations = [
        migrations.RunPython(tirar_cerquilha, migrations.RunPython.noop),
    ]
