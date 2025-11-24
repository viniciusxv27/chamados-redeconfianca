from django import forms
from .models import Lesson, QuizQuestion, QuizOption


class LessonForm(forms.ModelForm):
    """Formulário para criar e editar lições"""
    
    class Meta:
        model = Lesson
        fields = [
            'title', 
            'description', 
            'lesson_type', 
            'content', 
            'video_url', 
            'video_file',
            'document_file',
            'duration_minutes', 
            'points', 
            'order',
            'is_required'
        ]
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300',
                'placeholder': 'Ex: Introdução ao Sistema'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300 resize-none',
                'rows': 3,
                'placeholder': 'Descreva o conteúdo desta lição...'
            }),
            'lesson_type': forms.Select(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300'
            }),
            'content': forms.Textarea(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300',
                'rows': 10,
                'placeholder': 'Conteúdo da lição em HTML ou texto...'
            }),
            'video_url': forms.URLInput(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300',
                'placeholder': 'https://youtube.com/watch?v=...'
            }),
            'video_file': forms.FileInput(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 outline-none transition-all duration-300',
                'accept': 'video/*'
            }),
            'document_file': forms.FileInput(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 outline-none transition-all duration-300',
                'accept': '.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx'
            }),
            'duration_minutes': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300',
                'min': '0'
            }),
            'points': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300',
                'min': '0'
            }),
            'order': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300',
                'min': '0'
            }),
            'is_required': forms.CheckboxInput(attrs={
                'class': 'w-5 h-5 text-purple-600 border-2 border-gray-300 rounded focus:ring-2 focus:ring-purple-200'
            })
        }
        labels = {
            'title': 'Título da Lição',
            'description': 'Descrição',
            'lesson_type': 'Tipo de Lição',
            'content': 'Conteúdo',
            'video_url': 'URL do Vídeo (YouTube, Vimeo, etc)',
            'video_file': 'Arquivo de Vídeo',
            'document_file': 'Documento (PDF, DOC, PPT, XLS)',
            'duration_minutes': 'Duração (minutos)',
            'points': 'Pontuação',
            'order': 'Ordem',
            'is_required': 'Lição Obrigatória'
        }


class QuizQuestionForm(forms.ModelForm):
    """Formulário para criar e editar questões de quiz"""
    
    class Meta:
        model = QuizQuestion
        fields = ['question_text', 'points', 'order']
        widgets = {
            'question_text': forms.Textarea(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300 resize-none',
                'rows': 3,
                'placeholder': 'Digite a pergunta do quiz...'
            }),
            'points': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300',
                'min': '1',
                'value': '10'
            }),
            'order': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300',
                'min': '0'
            })
        }
        labels = {
            'question_text': 'Pergunta',
            'points': 'Pontos',
            'order': 'Ordem'
        }


class QuizOptionForm(forms.ModelForm):
    """Formulário para criar e editar alternativas de quiz"""
    
    class Meta:
        model = QuizOption
        fields = ['option_text', 'is_correct']
        widgets = {
            'option_text': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-purple-500 focus:ring-2 focus:ring-purple-200 outline-none transition-all duration-300',
                'placeholder': 'Digite a alternativa...'
            }),
            'is_correct': forms.CheckboxInput(attrs={
                'class': 'w-5 h-5 text-green-600 border-2 border-gray-300 rounded focus:ring-2 focus:ring-green-200'
            })
        }
        labels = {
            'option_text': 'Alternativa',
            'is_correct': 'Resposta Correta'
        }


# Formsets para gerenciar múltiplas questões e alternativas
from django.forms import inlineformset_factory

QuizQuestionFormSet = inlineformset_factory(
    Lesson,
    QuizQuestion,
    form=QuizQuestionForm,
    extra=1,
    can_delete=True
)

QuizOptionFormSet = inlineformset_factory(
    QuizQuestion,
    QuizOption,
    form=QuizOptionForm,
    extra=4,
    can_delete=True,
    min_num=2,
    validate_min=True
)
