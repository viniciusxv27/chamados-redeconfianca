"""As carteiras da planilha "QUADRO - REDE CONFIANÇA" (coordenação → lojas).

As lojas são achadas pelo nome do setor (sem acento e sem caixa). O que não
for achado fica em "Lojas sem carteira" e se ajusta na aba Carteiras.
"""
import unicodedata

from django.db import migrations

CARTEIRAS = [
    ('SERRÃO', 'João', ['NORTE SUL', 'MONTSERRAT', 'LARANJEIRAS', 'PORTO CANOA', 'JACARAIPE', 'SERRA SEDE']),
    ('VITÓRIA VELHA', 'Pedro', ['MASTERPLACE', 'CENTRO VIX', 'CENTRO DE VITORIA', 'CENTRO VILA VELHA',
                                'CENTRO DE VILA VELHA', 'GLORIA', 'MARCILIO', 'ITACIBA']),
    ('INTERIOR RJ/ES', 'Luiz', ['ANCHIETA', 'PIUMA', 'ICONHA', 'BOM JESUS', 'PADUA', 'MIRACEMA', 'FIDELIS',
                                'QUISSAMA']),
    ('SPC', 'Thayandra', ['ITAIM', 'EMBU', 'CARAPICUIBA']),
]


def _norm(texto):
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    return ' '.join(''.join(c for c in texto if not unicodedata.combining(c)).upper().split())


def criar(apps, schema_editor):
    Carteira = apps.get_model('quadro_hc', 'Carteira')
    Sector = apps.get_model('users', 'Sector')
    User = apps.get_model('users', 'User')
    if Carteira.objects.exists():
        return
    setores = list(Sector.objects.all())
    coordenadores = list(User.objects.filter(is_active=True, job_title__icontains='COORDENADOR'))
    usados = set()
    for ordem, (nome, coordenacao, chaves) in enumerate(CARTEIRAS, start=1):
        coord = next((u for u in coordenadores if _norm(u.first_name).split()[:1] == [_norm(coordenacao)]), None)
        carteira = Carteira.objects.create(nome=nome, coordenador=coord, coordenador_nome=coordenacao, ordem=ordem)
        lojas = [s for s in setores if s.id not in usados and any(k in _norm(s.name) for k in chaves)]
        usados.update(s.id for s in lojas)
        carteira.lojas.set(lojas)


def desfazer(apps, schema_editor):
    apps.get_model('quadro_hc', 'Carteira').objects.filter(nome__in=[c[0] for c in CARTEIRAS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('quadro_hc', '0001_initial'),
        ('users', '0044_display_reference_phase'),
    ]
    operations = [migrations.RunPython(criar, desfazer)]
