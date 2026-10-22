from django.db import migrations

# Lojas de SP, que não estavam na planilha "Acompanhamento E.A" (22/10/2026): ADABAS -> CNPJ VIVO, NOME LOJAS.
LOJAS_VIVO_SP = [
    ('SPREV002-003', '09.163.602/0028-54', 'VIVO CARAPICUIBA SP'),
    ('SPREV002-002', '09.163.602/0027-73', 'VIVO EMBU DAS ARTES SP'),
    ('SPREV002-001', '09.163.602/0026-92', 'VIVO ITAIM PAULISTA SP'),
]


def preencher(apps, schema_editor):
    Sector = apps.get_model('users', 'Sector')
    for adabas, cnpj, nome in LOJAS_VIVO_SP:
        Sector.objects.filter(adabas__iexact=adabas).update(cnpj_vivo=cnpj, nome_loja_vivo=nome)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0048_setor_cnpj_nome_vivo'),
    ]

    operations = [
        migrations.RunPython(preencher, migrations.RunPython.noop),
    ]
