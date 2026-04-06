from django import forms
from django.contrib.auth.models import Group

from users.models import Sector, User

from .models import PowerBIReport


class PowerBIReportForm(forms.ModelForm):
    allowed_groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by('name'),
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
        widget=forms.SelectMultiple(attrs={'class': 'w-full border border-gray-300 rounded-lg p-2 h-36'}),
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
            'icon_class': forms.TextInput(attrs={'class': 'w-full border border-gray-300 rounded-lg p-2'}),
            'embed_url': forms.URLInput(attrs={'class': 'w-full border border-gray-300 rounded-lg p-2'}),
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
