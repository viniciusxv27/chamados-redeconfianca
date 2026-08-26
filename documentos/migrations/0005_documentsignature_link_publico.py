"""Link público de assinatura, um por signatário.

Escrita à mão porque `public_token` é único e a tabela já tem registros: o
autodetector pediria um valor único para todas as linhas existentes, o que ele
não sabe gerar. Aqui o campo entra sem restrição, cada linha recebe seu próprio
UUID e só então a unicidade é aplicada.
"""
import uuid

from django.db import migrations, models


def semear_tokens(apps, schema_editor):
    Assinatura = apps.get_model('documentos', 'DocumentSignature')
    # Um UPDATE por linha: são centenas, não milhões, e cada uma precisa de um
    # valor diferente — não há como fazer num único UPDATE.
    for pk in Assinatura.objects.values_list('pk', flat=True).iterator():
        Assinatura.objects.filter(pk=pk).update(public_token=uuid.uuid4())


def nada(apps, schema_editor):
    """Voltar atrás não precisa fazer nada: a coluna inteira é removida."""


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0004_document_assinatura_obrigatoria_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='documentsignature',
            name='public_token',
            field=models.UUIDField(default=uuid.uuid4, editable=False,
                                   verbose_name='Token do link público'),
        ),
        migrations.RunPython(semear_tokens, nada),
        migrations.AlterField(
            model_name='documentsignature',
            name='public_token',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True,
                                   db_index=True, verbose_name='Token do link público'),
        ),
        migrations.AddField(
            model_name='documentsignature',
            name='public_enabled',
            field=models.BooleanField(
                default=True, verbose_name='Link público ativo',
                help_text='Desligue para invalidar o endereço já enviado.'),
        ),
        migrations.AddField(
            model_name='documentsignature',
            name='signed_via_link',
            field=models.BooleanField(
                default=False, verbose_name='Assinado pelo link público',
                help_text='Assinatura feita sem login no portal. Fica registrado para '
                          'nunca ser confundida com uma assinatura autenticada.'),
        ),
        migrations.AddField(
            model_name='documentsignature',
            name='signer_declared_name',
            field=models.CharField(
                blank=True, max_length=150, verbose_name='Nome declarado por quem assinou',
                help_text='Digitado por quem abriu o link, como afirmação de identidade.'),
        ),
        migrations.AddField(
            model_name='documentsignature',
            name='signer_declared_doc',
            field=models.CharField(
                blank=True, max_length=30, verbose_name='CPF declarado por quem assinou'),
        ),
    ]
