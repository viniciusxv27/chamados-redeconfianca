"""Tabela de avaliação inicial (iPhone) e a categoria do chamado do Renova.

Os valores são os do impresso "Tabela de avaliação" (padrão A); B, C e D saem
dos descontos configurados (20%, 40% e 60%).
"""
from decimal import Decimal

from django.db import migrations

CATEGORIA_RENOVA = 89

PRECOS = [
    ('iPhone 13', '128GB', '1000'), ('iPhone 13', '256GB', '1200'),
    ('iPhone 13 Pro', '128GB', '1400'), ('iPhone 13 Pro', '256GB', '1600'),
    ('iPhone 13 Pro Max', '128GB', '1700'), ('iPhone 13 Pro Max', '256GB', '1900'),
    ('iPhone 14', '128GB', '1400'), ('iPhone 14', '256GB', '1600'),
    ('iPhone 14 Plus', '128GB', '1600'), ('iPhone 14 Plus', '256GB', '1800'),
    ('iPhone 14 Pro', '128GB', '1900'), ('iPhone 14 Pro', '256GB', '2100'),
    ('iPhone 14 Pro Max', '128GB', '2400'), ('iPhone 14 Pro Max', '256GB', '2600'),
    ('iPhone 15', '128GB', '2000'), ('iPhone 15', '256GB', '2200'),
    ('iPhone 15 Plus', '128GB', '2200'), ('iPhone 15 Plus', '256GB', '2400'),
    ('iPhone 15 Pro', '128GB', '2400'), ('iPhone 15 Pro', '256GB', '2600'),
    ('iPhone 15 Pro Max', '256GB', '3000'),
    ('iPhone 16', '128GB', '2600'), ('iPhone 16', '256GB', '2800'),
    ('iPhone 16 Plus', '128GB', '2800'), ('iPhone 16 Plus', '256GB', '3000'),
    ('iPhone 16 Pro', '128GB', '3200'), ('iPhone 16 Pro', '256GB', '3400'),
    ('iPhone 16 Pro Max', '256GB', '4000'), ('iPhone 16 Pro Max', '512GB', '4200'),
    ('iPhone 17', '256GB', '3200'),
    ('iPhone 17 Pro', '256GB', '4700'), ('iPhone 17 Pro', '512GB', '4900'),
    ('iPhone 17 Pro Max', '256GB', '5500'), ('iPhone 17 Pro Max', '512GB', '5900'),
]


def semear(apps, schema_editor):
    Preco = apps.get_model('renova', 'PrecoAparelho')
    for posicao, (modelo, armazenamento, valor) in enumerate(PRECOS, start=1):
        Preco.objects.update_or_create(
            marca='APPLE', modelo=modelo, armazenamento=armazenamento,
            defaults={'valor_excelente': Decimal(valor), 'ordem': posicao * 10, 'ativo': True})

    Configuracao = apps.get_model('renova', 'ConfiguracaoRenova')
    Category = apps.get_model('tickets', 'Category')
    cfg, _ = Configuracao.objects.get_or_create(pk=1)
    if cfg.categoria_id is None and Category.objects.filter(pk=CATEGORIA_RENOVA).exists():
        cfg.categoria_id = CATEGORIA_RENOVA
        cfg.save(update_fields=['categoria'])


def desfazer(apps, schema_editor):
    Preco = apps.get_model('renova', 'PrecoAparelho')
    for modelo, armazenamento, _ in PRECOS:
        Preco.objects.filter(marca='APPLE', modelo=modelo, armazenamento=armazenamento).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('renova', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(semear, desfazer),
    ]
