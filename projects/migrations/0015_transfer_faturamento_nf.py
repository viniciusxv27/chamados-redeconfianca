"""Substitui "Material" por "Faturamento 87" e cria o "Número da NF".

Usa RenameField (e não remover/criar) para preservar o que já foi digitado no
campo Material — a troca é de rótulo/uso, não de conteúdo.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0014_transfer_material'),
    ]

    operations = [
        migrations.RenameField(
            model_name='supporttransferrequest',
            old_name='material',
            new_name='faturamento_87',
        ),
        migrations.AlterField(
            model_name='supporttransferrequest',
            name='faturamento_87',
            field=models.CharField(blank=True, default='', max_length=60,
                                   verbose_name='Faturamento 87'),
        ),
        migrations.AddField(
            model_name='supporttransferrequest',
            name='nf_number',
            field=models.CharField(blank=True, default='', max_length=40,
                                   verbose_name='Número da NF'),
        ),
    ]
