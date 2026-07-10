from django import forms

from users.models import User, Sector
from .checkers import available_checkers
from .models import PortalPopup


# Hierarquias oferecidas na segmentação (mesmos códigos de User.HIERARCHY_CHOICES).
HIERARCHY_CHOICES = User.HIERARCHY_CHOICES


class PortalPopupForm(forms.ModelForm):
    target_hierarchies = forms.MultipleChoiceField(
        choices=HIERARCHY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Hierarquias',
    )

    class Meta:
        model = PortalPopup
        fields = [
            'title', 'message', 'icon', 'color',
            'completion_mode', 'action_url', 'action_label', 'external_check_key',
            'target_all', 'target_users', 'target_sectors', 'target_hierarchies',
            'blocking_mode', 'block_after',
            'is_active', 'start_at', 'end_at', 'order',
        ]
        widgets = {
            'message': forms.Textarea(attrs={'rows': 4}),
            'block_after': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'start_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'target_users': forms.SelectMultiple(attrs={'size': 8}),
            'target_sectors': forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['target_users'].queryset = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
        self.fields['target_sectors'].queryset = Sector.objects.order_by('name')

        # Os checkers de tarefa externa são descobertos em runtime.
        checkers = available_checkers()
        choices = [('', '— selecione —')] + checkers
        self.fields['external_check_key'] = forms.ChoiceField(
            choices=choices, required=False, label='Verificação da tarefa',
        )

        # Estilo Tailwind consistente nos campos de texto/seleção.
        base = 'w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500'
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.CheckboxSelectMultiple, forms.CheckboxInput)):
                continue
            css = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (css + ' ' + base).strip()

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get('completion_mode')
        action_url = (cleaned.get('action_url') or '').strip()
        check_key = cleaned.get('external_check_key')
        blocking_mode = cleaned.get('blocking_mode')

        if mode in (PortalPopup.MODE_LINK, PortalPopup.MODE_EXTERNAL) and not action_url:
            self.add_error('action_url', 'Informe a URL de ação para este modo de conclusão.')

        if mode == PortalPopup.MODE_EXTERNAL and not check_key:
            self.add_error('external_check_key', 'Selecione qual tarefa marca o popup como concluído.')

        if blocking_mode == PortalPopup.BLOCK_AFTER and not cleaned.get('block_after'):
            self.add_error('block_after', 'Informe a data/hora a partir da qual o popup bloqueia.')

        if not cleaned.get('target_all'):
            has_target = (
                cleaned.get('target_users') or cleaned.get('target_sectors')
                or cleaned.get('target_hierarchies')
            )
            if not has_target:
                raise forms.ValidationError(
                    'Defina o público: marque "Todos os usuários" ou selecione '
                    'usuários, setores ou hierarquias.'
                )
        return cleaned
