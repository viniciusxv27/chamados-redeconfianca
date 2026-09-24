"""A diferença entra no saldo: refaz o saldo já gravado de todas as lojas.

O saldo do dia passou a ser ``saldo anterior + Valor real + Diferença −
Depósito`` — e Valor real + Diferença é a Entrada. Como o saldo é um campo
gravado (para a tela não recalcular a série toda), os dias que já estão no
banco continuariam com a conta antiga, que era exatamente onde o problema
aparecia: sem nenhuma loja ter contado um dia, o saldo ficava R$ 0,00 em toda
parte mesmo com Entrada e Diferença na linha.

Usa o serviço de verdade (e não o modelo histórico) porque a conta mora numa
property do modelo, que a migração histórica não tem. Rodar de novo não faz mal
nenhum: o recálculo é idempotente.
"""
from django.db import migrations


def refazer_saldos(apps, schema_editor):
    from contagem_caixa.servicos import recalcular_saldos

    Dia = apps.get_model('contagem_caixa', 'ContagemCaixaDia')
    lojas = sorted(set(Dia.objects.values_list('loja_id', flat=True)))
    for loja_id in lojas:
        recalcular_saldos(loja_id)


class Migration(migrations.Migration):

    dependencies = [
        ('contagem_caixa', '0005_saldoinicialmes'),
    ]

    operations = [
        # Sem volta: o saldo antigo é o que esta migração está corrigindo.
        migrations.RunPython(refazer_saldos, migrations.RunPython.noop),
    ]
