from django import forms
from .models import Asset


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            'patrimonio_numero', 
            'nome', 
            'localizado', 
            'setor', 
            'pdv', 
            'estado_fisico', 
            'observacoes'
        ]
        widgets = {
            'patrimonio_numero': forms.TextInput(attrs={
                'class': 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500',
                'placeholder': 'Ex: PAT001'
            }),
            'nome': forms.TextInput(attrs={
                'class': 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500',
                'placeholder': 'Nome do ativo'
            }),
            'localizado': forms.TextInput(attrs={
                'class': 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500',
                'placeholder': 'Local onde o ativo está localizado'
            }),
            'setor': forms.TextInput(attrs={
                'class': 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500',
                'placeholder': 'Setor responsável'
            }),
            'pdv': forms.TextInput(attrs={
                'class': 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500',
                'placeholder': 'PDV'
            }),
            'estado_fisico': forms.Select(attrs={
                'class': 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500'
            }),
            'observacoes': forms.Textarea(attrs={
                'class': 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500',
                'rows': 4,
                'placeholder': 'Observações adicionais sobre o ativo'
            }),
        }

    def clean_patrimonio_numero(self):
        patrimonio_numero = self.cleaned_data['patrimonio_numero']
        if Asset.objects.filter(patrimonio_numero=patrimonio_numero).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Já existe um ativo com este número de patrimônio.')
        return patrimonio_numero
