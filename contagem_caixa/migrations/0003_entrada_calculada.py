from django.db import migrations


class Migration(migrations.Migration):
    """A Entrada virou conta; o campo digitado passa a se chamar entrada_manual.

    É um RenameField de propósito. O autodetector do Django propôs remover a
    coluna e criar outra, o que jogaria fora o que já tivesse sido digitado —
    numa tabela de caixa, apagar valor lançado é o pior desfecho possível.
    """

    dependencies = [
        ('contagem_caixa', '0002_alter_contagemcaixadia_valor_vivogo'),
    ]

    operations = [
        migrations.RenameField(
            model_name='contagemcaixadia',
            old_name='entrada',
            new_name='entrada_manual',
        ),
    ]
