from django.db import migrations, models


class Migration(migrations.Migration):
    """Renomeia nome_funcionario -> nome em FeriasLancamento.

    RenameField (não Remove+Add): os 65 lançamentos mantêm o nome gravado.
    A tabela de ponto já usa 'nome'; as duas passam a falar a mesma língua.
    """

    dependencies = [('tangerino', '0005_ponto_por_dia')]

    operations = [
        migrations.RenameField(
            model_name='feriaslancamento', old_name='nome_funcionario', new_name='nome'),
        migrations.AlterField(
            model_name='feriaslancamento', name='nome',
            field=models.CharField(blank=True, db_index=True, max_length=200,
                                   verbose_name='Nome')),
    ]
