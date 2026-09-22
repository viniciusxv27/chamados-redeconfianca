from django.contrib import admin

from .models import Alternativa, Participante, Pergunta, Quiz, Resposta, Sala


class AlternativaInline(admin.TabularInline):
    model = Alternativa
    extra = 0


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'categoria', 'status', 'criado_por', 'atualizado_em')
    list_filter = ('status', 'categoria')
    search_fields = ('titulo', 'descricao', 'categoria')


@admin.register(Pergunta)
class PerguntaAdmin(admin.ModelAdmin):
    list_display = ('enunciado', 'quiz', 'ordem', 'tempo_limite')
    list_filter = ('quiz',)
    search_fields = ('enunciado',)
    inlines = [AlternativaInline]


@admin.register(Sala)
class SalaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'codigo', 'quiz', 'agendada_para', 'fase', 'responsavel')
    list_filter = ('fase',)
    search_fields = ('codigo', 'nome', 'quiz__titulo')


@admin.register(Participante)
class ParticipanteAdmin(admin.ModelAdmin):
    list_display = ('user', 'sala', 'origem', 'entrou_em', 'pontos', 'acertos', 'erros')
    list_filter = ('origem',)
    search_fields = ('user__first_name', 'user__last_name', 'sala__codigo')


@admin.register(Resposta)
class RespostaAdmin(admin.ModelAdmin):
    list_display = ('participante', 'pergunta', 'alternativa', 'correta', 'pontos', 'tempo_ms')
    list_filter = ('correta',)
