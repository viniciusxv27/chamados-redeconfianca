"""Recorrência de metas + fluxo de solicitação/aprovação.

Escrita à mão de propósito: o `makemigrations` não interativo não reconhece a
renomeação e geraria RemoveField + AddField, apagando a periodicidade das 37
metas existentes. Com RenameField os valores atravessam intactos.

A RunPython desliga a geração automática no acervo antigo. Aquelas metas foram
criadas quando "periodicidade" era só um rótulo sem efeito; ligar a recorrência
retroativamente faria 26 metas em aberto (11 delas diárias) começarem a se
multiplicar sozinhas assim que fossem concluídas. O valor escolhido continua
visível — só a geração fica desligada até alguém decidir o contrário.
"""
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


def desligar_recorrencia_do_acervo(apps, schema_editor):
    Meta = apps.get_model('impulso', 'Meta')
    Meta.objects.update(recorrencia_ativa=False)


def religar(apps, schema_editor):
    Meta = apps.get_model('impulso', 'Meta')
    Meta.objects.update(recorrencia_ativa=True)


class Migration(migrations.Migration):

    dependencies = [
        ('impulso', '0002_alter_meta_periodicidade_ciclo_ciclomes_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameField(
            model_name='meta', old_name='periodicidade', new_name='recorrencia'),
        migrations.AlterField(
            model_name='meta', name='recorrencia',
            field=models.CharField(
                choices=[('UNICA', 'Única vez'), ('DIARIA', 'Diária'),
                         ('SEMANAL', 'Semanal'), ('QUINZENAL', 'Quinzenal'),
                         ('MENSAL', 'Mensal')],
                default='UNICA', max_length=12, verbose_name='Recorrência'),
        ),
        migrations.AddField(
            model_name='meta', name='recorrencia_ativa',
            field=models.BooleanField(
                default=True, verbose_name='Gerar próxima ocorrência ao concluir'),
        ),
        # Só depois de a coluna existir é que dá para desligar o acervo.
        migrations.RunPython(desligar_recorrencia_do_acervo, religar),
        migrations.AddField(
            model_name='meta', name='recorrencia_de',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='ocorrencias', to='impulso.meta',
                verbose_name='Ocorrência anterior'),
        ),
        migrations.AddField(
            model_name='meta', name='aprovacao',
            field=models.CharField(
                choices=[('APROVADA', 'Aprovada'),
                         ('PENDENTE', 'Aguardando aprovação do gestor'),
                         ('RECUSADA', 'Recusada')],
                default='APROVADA', max_length=10, verbose_name='Aprovação'),
        ),
        migrations.AddField(
            model_name='meta', name='solicitada_por',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='impulso_metas_solicitadas', to=settings.AUTH_USER_MODEL,
                verbose_name='Solicitada por'),
        ),
        migrations.AddField(
            model_name='meta', name='decidida_por',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='impulso_metas_decididas', to=settings.AUTH_USER_MODEL,
                verbose_name='Decidida por'),
        ),
        migrations.AddField(
            model_name='meta', name='decidida_em',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Decidida em'),
        ),
        migrations.AddField(
            model_name='meta', name='motivo_recusa',
            field=models.TextField(blank=True, verbose_name='Motivo da recusa'),
        ),
    ]
