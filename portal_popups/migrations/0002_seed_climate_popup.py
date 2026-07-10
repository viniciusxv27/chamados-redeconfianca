import datetime

from django.db import migrations
from django.utils import timezone


CLIMATE_MESSAGE = (
    'Sua opinião é muito importante! Reserve alguns minutos para responder a '
    'Pesquisa de Clima Organizacional. É anônima e ajuda a melhorar o nosso dia a dia.'
)

# Mesmo prazo do gate antigo (sexta-feira, 10/07/2026). Até lá o popup pode ser
# pulado; depois passa a bloquear a navegação de quem não respondeu.
CLIMATE_DEADLINE = datetime.datetime(2026, 7, 10, 23, 59, 59)


def create_climate_popup(apps, schema_editor):
    PortalPopup = apps.get_model('portal_popups', 'PortalPopup')

    # Idempotente: não duplica se já existir um popup com esse checker.
    if PortalPopup.objects.filter(external_check_key='climate_survey').exists():
        return

    block_after = timezone.make_aware(CLIMATE_DEADLINE, timezone.get_current_timezone())

    PortalPopup.objects.create(
        title='Pesquisa de Clima Organizacional',
        message=CLIMATE_MESSAGE,
        icon='fas fa-clipboard-list',
        color='orange',
        completion_mode='EXTERNAL',
        action_url='/feedback/pesquisa-clima/',
        action_label='Responder pesquisa',
        external_check_key='climate_survey',
        target_all=True,
        target_hierarchies=[],
        blocking_mode='AFTER',
        block_after=block_after,
        is_active=True,
        order=0,
    )


def remove_climate_popup(apps, schema_editor):
    PortalPopup = apps.get_model('portal_popups', 'PortalPopup')
    PortalPopup.objects.filter(external_check_key='climate_survey').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('portal_popups', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_climate_popup, remove_climate_popup),
    ]
