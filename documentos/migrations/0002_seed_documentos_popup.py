from django.db import migrations


POPUP_MESSAGE = (
    'Você tem documentos aguardando a sua assinatura digital. '
    'Acesse a área de Documentos para revisar e assinar. '
    'A assinatura registra data, hora e IP, e gera o PDF com o certificado do portal.'
)


def create_documentos_popup(apps, schema_editor):
    PortalPopup = apps.get_model('portal_popups', 'PortalPopup')

    # Idempotente: não duplica se já existir um popup com esse checker.
    if PortalPopup.objects.filter(external_check_key='documentos_pendentes').exists():
        return

    PortalPopup.objects.create(
        title='Documentos pendentes de assinatura',
        message=POPUP_MESSAGE,
        icon='fas fa-file-signature',
        color='amber',
        completion_mode='EXTERNAL',
        action_url='/documentos/',
        action_label='Ver documentos',
        external_check_key='documentos_pendentes',
        target_all=True,
        target_hierarchies=[],
        blocking_mode='NEVER',
        is_active=True,
        order=10,
    )


def remove_documentos_popup(apps, schema_editor):
    PortalPopup = apps.get_model('portal_popups', 'PortalPopup')
    PortalPopup.objects.filter(external_check_key='documentos_pendentes').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0001_initial'),
        ('portal_popups', '0002_seed_climate_popup'),
    ]

    operations = [
        migrations.RunPython(create_documentos_popup, remove_documentos_popup),
    ]
