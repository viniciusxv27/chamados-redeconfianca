"""Cadastra o template "Rede Confiança" (material oficial com os fundos já limpos)."""
from django.db import migrations


def criar(apps, schema_editor):
    from apresentacoes.formato import sanear_documento
    from apresentacoes.padrao import ESTILO, NOME, documento_rede_confianca

    Template = apps.get_model('apresentacoes', 'TemplateApresentacao')
    if Template.objects.filter(origem='SISTEMA', nome=NOME).exists():
        return
    Template.objects.create(
        nome=NOME, descricao='Identidade visual oficial: capa, seção, conteúdo, quadro e encerramento.',
        estilo=ESTILO, documento=sanear_documento(documento_rede_confianca()), origem='SISTEMA',
        padrao_da_rede=True, principal=True, status='PRONTO')


class Migration(migrations.Migration):

    dependencies = [
        ('apresentacoes', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(criar, migrations.RunPython.noop),
    ]
