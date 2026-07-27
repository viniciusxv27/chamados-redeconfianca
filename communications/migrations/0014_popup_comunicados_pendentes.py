"""Cria o popup bloqueante que exige o 'de acordo' em comunicados não confirmados.

Segue o mesmo padrão da seed do clima (portal_popups/0002). O popup usa o checker
'comunicados_pendentes' (communications/popup_checkers.py) e bloqueia o portal
(exceto a seção /communications/) até o usuário confirmar todos os comunicados.
Pode ser desativado a qualquer momento em /popups/ (is_active).
"""
from django.db import migrations


POPUP_TITLE = 'Comunicados aguardando o seu "de acordo"'
POPUP_MESSAGE = (
    'Há comunicados que ainda precisam da sua confirmação. Abra cada comunicado e '
    'registre o seu "de acordo" (Estou Ciente) para continuar utilizando o portal.'
)


def create_popup(apps, schema_editor):
    PortalPopup = apps.get_model('portal_popups', 'PortalPopup')
    if PortalPopup.objects.filter(external_check_key='comunicados_pendentes').exists():
        return
    PortalPopup.objects.create(
        title=POPUP_TITLE,
        message=POPUP_MESSAGE,
        icon='fas fa-bullhorn',
        color='indigo',
        completion_mode='EXTERNAL',
        action_url='/communications/',
        action_label='Ver comunicados',
        external_check_key='comunicados_pendentes',
        target_all=True,
        target_hierarchies=[],
        blocking_mode='ALWAYS',
        is_active=True,
        order=10,
    )


def remove_popup(apps, schema_editor):
    PortalPopup = apps.get_model('portal_popups', 'PortalPopup')
    PortalPopup.objects.filter(external_check_key='comunicados_pendentes').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0013_communicationimage'),
        ('portal_popups', '0002_seed_climate_popup'),
    ]

    operations = [
        migrations.RunPython(create_popup, remove_popup),
    ]
