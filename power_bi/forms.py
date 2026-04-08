from django import forms
from communications.models import CommunicationGroup

from users.models import Sector, User

from .models import GoalUpload, PowerBIReport


ICON_CHOICES = [
    ('fas fa-chart-line', 'Grafico em linha'),
    ('fas fa-chart-bar', 'Grafico em barras'),
    ('fas fa-chart-pie', 'Grafico de pizza'),
    ('fas fa-table', 'Tabela'),
    ('fas fa-database', 'Base de dados'),
    ('fas fa-signal', 'Indicadores'),
    ('fas fa-bullseye', 'Metas'),
    ('fas fa-coins', 'Financeiro'),
    ('fas fa-store', 'Vendas'),
    ('fas fa-users', 'Pessoas'),
    ('fas fa-warehouse', 'Estoque'),
    ('fas fa-headset', 'Atendimento'),
]


class PowerBIReportForm(forms.ModelForm):
    allowed_groups = forms.ModelMultipleChoiceField(
        queryset=CommunicationGroup.objects.filter(is_active=True).order_by('name'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Grupos'
    )
    allowed_sectors = forms.ModelMultipleChoiceField(
        queryset=Sector.objects.order_by('name'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Setores'
    )
    allowed_users = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        required=False,
        widget=forms.SelectMultiple(attrs={'id': 'id_allowed_users'}),
        label='Usuarios especificos'
    )
    allowed_hierarchies = forms.MultipleChoiceField(
        choices=User.HIERARCHY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Hierarquias'
    )

    class Meta:
        model = PowerBIReport
        fields = [
            'name',
            'description',
            'icon_class',
            'embed_url',
            'card_background_image',
            'allow_open_in_new_tab',
            'sort_order',
            'is_active',
            'allowed_groups',
            'allowed_sectors',
            'allowed_hierarchies',
            'allowed_users',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full border border-gray-300 rounded-lg p-2'}),
            'description': forms.Textarea(attrs={'class': 'w-full border border-gray-300 rounded-lg p-2', 'rows': 3}),
            'icon_class': forms.Select(
                attrs={
                    'class': 'w-full border border-gray-300 rounded-lg p-2',
                    'id': 'id_icon_class',
                },
                choices=ICON_CHOICES,
            ),
            'embed_url': forms.URLInput(attrs={'class': 'w-full border border-gray-300 rounded-lg p-2'}),
            'card_background_image': forms.ClearableFileInput(
                attrs={
                    'class': 'w-full border border-gray-300 rounded-lg p-2',
                    'accept': 'image/*',
                }
            ),
            'allow_open_in_new_tab': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-primary'}),
            'sort_order': forms.NumberInput(attrs={'class': 'w-full border border-gray-300 rounded-lg p-2', 'min': 0}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-primary'}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.allowed_hierarchies = self.cleaned_data.get('allowed_hierarchies', [])
        if commit:
            instance.save()
            self.save_m2m()
        return instance


MONTH_CHOICES = [
    (1, 'Janeiro'),
    (2, 'Fevereiro'),
    (3, 'Marco'),
    (4, 'Abril'),
    (5, 'Maio'),
    (6, 'Junho'),
    (7, 'Julho'),
    (8, 'Agosto'),
    (9, 'Setembro'),
    (10, 'Outubro'),
    (11, 'Novembro'),
    (12, 'Dezembro'),
]


class GoalUploadForm(forms.ModelForm):
    year = forms.IntegerField(
        min_value=2000,
        max_value=2100,
        widget=forms.NumberInput(attrs={'class': 'w-full border border-gray-300 rounded-lg p-2'})
    )
    month = forms.ChoiceField(
        choices=MONTH_CHOICES,
        widget=forms.Select(attrs={'class': 'w-full border border-gray-300 rounded-lg p-2'})
    )
    file = forms.FileField(
        required=True,
        widget=forms.FileInput(attrs={
            'class': 'w-full border border-gray-300 rounded-lg p-2',
            'accept': '.xlsx,.xlsm,.xltx,.xltm'
        }),
        label='Arquivo Excel'
    )
    fixa_as_percentage = forms.BooleanField(
        required=False,
        label='Quantidade em Fixa',
        widget=forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-primary'})
    )

    class Meta:
        model = GoalUpload
        fields = ['year', 'month', 'fixa_as_percentage']

    def clean_month(self):
        return int(self.cleaned_data['month'])

    def clean_file(self):
        uploaded = self.cleaned_data['file']
        name = (uploaded.name or '').lower()
        if not name.endswith(('.xlsx', '.xlsm', '.xltx', '.xltm')):
            raise forms.ValidationError('Envie um arquivo Excel valido (.xlsx).')
        return uploaded
