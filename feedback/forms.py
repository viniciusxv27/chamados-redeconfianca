from django import forms

from .models import Feedback, FeedbackAssignment


SCALE_FIELDS = [
    'nota_comunicacao',
    'nota_trabalho_equipe',
    'nota_organizacao',
    'nota_ferramentas_recursos',
    'nota_iniciativa',
    'nota_mudancas',
    'nota_conflitos',
]


class FeedbackForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = [
            'setor_area', 'data', 'nome_colaborador', 'gestor_imediato', 'gestor_mediato',
            'pontos_fortes', 'oportunidades_melhoria', 'acoes_propostas',
            'nota_comunicacao', 'nota_trabalho_equipe', 'nota_organizacao',
            'comunicacao_clara_texto',
            'nota_ferramentas_recursos', 'nota_iniciativa',
            'nota_mudancas', 'nota_conflitos',
            'cumpriu_metas_texto', 'suporte_orientacao_texto',
            'evolution_notes',
        ]
        widgets = {
            'data': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'setor_area': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'nome_colaborador': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'gestor_imediato': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'gestor_mediato': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'pontos_fortes': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'oportunidades_melhoria': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'acoes_propostas': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'comunicacao_clara_texto': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'cumpriu_metas_texto': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'suporte_orientacao_texto': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
            'evolution_notes': forms.Textarea(attrs={'rows': 4, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in SCALE_FIELDS:
            self.fields[f].widget = forms.NumberInput(attrs={
                'min': 0, 'max': 10, 'step': 1,
                'class': 'w-24 px-3 py-2 border border-gray-300 rounded-lg text-center focus:ring-2 focus:ring-primary',
            })


class AssignmentForm(forms.Form):
    evaluator = forms.IntegerField(widget=forms.HiddenInput())
    evaluatees = forms.CharField(
        widget=forms.HiddenInput(),
        help_text='IDs dos avaliados separados por vírgula.',
    )
    due_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg'}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg'}),
    )
