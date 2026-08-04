"""Preenche o ADABAS das lojas a partir da planilha "Dados Lojas.xlsx".

O mapeamento fica embutido aqui (e não lendo o arquivo) para a carga ser
reprodutível em qualquer ambiente e ficar registrada no histórico.

Casaram pelo nome: 18 lojas. Confirmados manualmente: CENTRO VITORIA ->
Loja Centro VIX e VIANA -> Loja Marcilio De Noronha.

Sem ADABAS de propósito (ficam vazios, conforme combinado):
  - Loja Itaciba — a linha "MASTERCEL ALTO LAGE" (ESD0267-010) da planilha
    não foi confirmada como sendo esta loja.
Setores que não são loja também permanecem vazios.
"""
from django.db import migrations


# nome do setor -> código ADABAS
ADABAS_POR_SETOR = {
    'Loja Anchieta': 'ESD0267-013',
    'Loja Bom Jesus do Itabapoana': 'RJD1220-002',
    'Loja Centro Vila Velha': 'ESD0267-014',
    'Loja Centro VIX': 'ESD0267-018',
    'Loja Glória': 'ESD0267-017',
    'Loja Iconha': 'ESD0267-012',
    'Loja Jacaraípe': 'ESD0267-009',
    'Loja Jardim Camburi': 'ESD0267-006',
    'Loja Laranjeiras': 'ESLA013-001',
    'Loja Marcilio De Noronha': 'ESD0267-007',
    'Loja Masterplace': 'ESD0386-001',
    'Loja Miracema': 'RJD1220-001',
    'Loja Montserrat': 'ESD0267-004',
    'Loja Norte Sul': 'ESD0267-015',
    'Loja Piuma': 'ESD0267-011',
    'Loja Porto Canoa': 'ESD0267-008',
    'Loja Quissama': 'RJD1220-003',
    'Loja Santo Antonio de Padua': 'RJD1220-004',
    'Loja Sao Fidelis': 'RJD1220-005',
    'Loja Serra Sede': 'ESD0267-016',
}


def preencher(apps, schema_editor):
    Sector = apps.get_model('users', 'Sector')
    for nome, codigo in ADABAS_POR_SETOR.items():
        # Só grava se o setor existir e ainda estiver vazio (não sobrescreve
        # um ajuste manual feito depois).
        Sector.objects.filter(name=nome, adabas='').update(adabas=codigo)


def limpar(apps, schema_editor):
    Sector = apps.get_model('users', 'Sector')
    Sector.objects.filter(adabas__in=list(ADABAS_POR_SETOR.values())).update(adabas='')


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0028_sector_adabas'),
    ]

    operations = [
        migrations.RunPython(preencher, limpar),
    ]
