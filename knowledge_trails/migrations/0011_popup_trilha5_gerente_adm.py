"""Popup bloqueante: membros do grupo 'Gerente / ADM' devem concluir a trilha 5
(FUNÇÕES SAP) para continuar navegando no portal.

Segue o padrão da seed do clima (portal_popups/0002). O gate por grupo e a
verificação de conclusão ficam no checker 'trilha5_gerente_adm'
(knowledge_trails/popup_checkers.py, registrado em knowledge_trails/apps.py::ready).
action_url='/trilhas/' libera toda a seção de trilhas (as lições ficam em
/trilhas/lesson/<id>/, fora de /trilhas/trail/5/) para evitar deadlock.
Pode ser desativado a qualquer momento em /popups/ (is_active).
"""
from django.db import migrations


POPUP_TITLE = 'Conclua a trilha "FUNÇÕES SAP" para continuar'
POPUP_MESSAGE = (
    'Para continuar utilizando o portal, é necessário concluir a trilha '
    '"FUNÇÕES SAP". Clique no botão abaixo, abra a trilha e finalize todas as '
    'lições. Assim que concluir, este aviso é liberado automaticamente.'
)


def create_popup(apps, schema_editor):
    PortalPopup = apps.get_model('portal_popups', 'PortalPopup')
    if PortalPopup.objects.filter(external_check_key='trilha5_gerente_adm').exists():
        return
    PortalPopup.objects.create(
        title=POPUP_TITLE,
        message=POPUP_MESSAGE,
        icon='fas fa-graduation-cap',
        color='indigo',
        completion_mode='EXTERNAL',
        action_url='/trilhas/',
        action_label='Ir para as trilhas',
        external_check_key='trilha5_gerente_adm',
        target_all=True,
        target_hierarchies=[],
        blocking_mode='ALWAYS',
        is_active=True,
        order=20,
    )


def remove_popup(apps, schema_editor):
    PortalPopup = apps.get_model('portal_popups', 'PortalPopup')
    PortalPopup.objects.filter(external_check_key='trilha5_gerente_adm').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge_trails', '0010_quizanswer_answer_text_quizanswer_awarded_points_and_more'),
        ('portal_popups', '0002_seed_climate_popup'),
    ]

    operations = [
        migrations.RunPython(create_popup, remove_popup),
    ]
