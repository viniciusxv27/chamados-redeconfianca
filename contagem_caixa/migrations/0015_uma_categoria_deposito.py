"""Uma categoria de depósito só.

A migração 0013 criou "DEPÓSITO" (a que manda o valor para a coluna Depósito)
com get_or_create pelo nome exato, e já existia uma "Depósito" criada à mão —
na tela de sangria apareciam as duas. Fica uma, chamada "Depósito" e marcada
para ir à coluna Depósito; as sangrias das outras passam para ela.
"""
import unicodedata

from django.db import migrations


def _chave(texto):
    t = unicodedata.normalize('NFKD', str(texto or ''))
    return ' '.join(''.join(c for c in t if not unicodedata.combining(c)).lower().split())


def unir(apps, schema_editor):
    Categoria = apps.get_model('contagem_caixa', 'CategoriaSangria')
    Sangria = apps.get_model('contagem_caixa', 'Sangria')
    iguais = [c for c in Categoria.objects.order_by('id') if _chave(c.nome) == 'deposito']
    if not iguais:
        return
    # Fica a que já manda para a coluna Depósito (a da 0013); senão a mais usada.
    fica = next((c for c in iguais if c.vai_para_deposito), None) or max(
        iguais, key=lambda c: Sangria.objects.filter(categoria_id=c.id).count())
    for outra in iguais:
        if outra.id == fica.id:
            continue
        Sangria.objects.filter(categoria_id=outra.id).update(categoria_id=fica.id)
        outra.delete()
    fica.nome = 'Depósito'
    fica.vai_para_deposito = True
    fica.ativa = True
    fica.save(update_fields=['nome', 'vai_para_deposito', 'ativa'])


class Migration(migrations.Migration):
    dependencies = [('contagem_caixa', '0014_allied_quantidade')]
    operations = [migrations.RunPython(unir, migrations.RunPython.noop)]
