import re
from datetime import datetime

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

_PARA = re.compile(r'para (\d{2}/\d{2}/\d{4}|sem data)\s*$')


def marcar_agendamentos_manuais(apps, schema_editor):
    """Quem já mudou o agendamento na lista passa a ser agendamento manual — e recebe de volta a data que escolheu.

    Até aqui a importação da Esteira gravava a DATA_AGENDA por cima da data posta na lista (em 19/11/2026,
    6 vendas tinham perdido a data escolhida em 18/11). A data vem do último histórico
    "Agendamento alterado na lista: X para Y" de cada venda. Idempotente.
    """
    Hist = apps.get_model('fibras', 'FibraStatusHistory')
    Fibra = apps.get_model('fibras', 'Fibra')
    ultimos = {}
    for fid, retorno, em, por in (Hist.objects.filter(retorno__startswith='Agendamento alterado na lista:')
                                  .order_by('fibra_id', 'alterado_em', 'id')
                                  .values_list('fibra_id', 'retorno', 'alterado_em', 'alterado_por_id')):
        ultimos[fid] = (retorno, em, por)
    for fid, (retorno, em, por) in ultimos.items():
        m = _PARA.search(retorno or '')
        if not m:
            continue
        data = None if m.group(1) == 'sem data' else datetime.strptime(m.group(1), '%d/%m/%Y').date()
        Fibra.objects.filter(pk=fid, agendamento_manual=False).update(
            agendamento_manual=True, agendamento_manual_em=em, agendamento_manual_por_id=por, data_agendamento=data)


def desfazer(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    """Colunas novas com db_default/nullable: os servidores que ainda rodam o código anterior seguem funcionando."""

    dependencies = [
        ('fibras', '0007_parecer_manual_e_agendamento'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='fibra',
            name='agendamento_manual',
            field=models.BooleanField(db_default=False, default=False, verbose_name='Agendamento definido à mão'),
        ),
        migrations.AddField(
            model_name='fibra',
            name='agendamento_manual_em',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Agendamento manual em'),
        ),
        migrations.AddField(
            model_name='fibra',
            name='agendamento_manual_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='+', to=settings.AUTH_USER_MODEL,
                                    verbose_name='Agendamento manual por'),
        ),
        migrations.RunPython(marcar_agendamentos_manuais, desfazer),
    ]
