from django.urls import path

from . import views

app_name = 'quiz'

urlpatterns = [
    # Participante
    path('', views.inicio, name='inicio'),
    path('entrar/', views.entrar, name='entrar'),
    path('sala/<str:codigo>/', views.jogar, name='jogar'),
    path('sala/<str:codigo>/estado/', views.estado, name='estado'),
    path('sala/<str:codigo>/responder/', views.responder, name='responder'),
    path('sala/<str:codigo>/meu-resultado/', views.meu_resultado, name='meu_resultado'),

    # Gestão: quizzes e perguntas
    path('quizzes/', views.quizzes, name='quizzes'),
    path('quizzes/novo/', views.quiz_novo, name='quiz_novo'),
    path('quizzes/<int:pk>/', views.quiz_detalhe, name='quiz'),
    path('quizzes/<int:pk>/editar/', views.quiz_editar, name='quiz_editar'),
    path('quizzes/<int:pk>/publicar/', views.quiz_publicar, name='quiz_publicar'),
    path('quizzes/<int:pk>/rascunho/', views.quiz_rascunho, name='quiz_rascunho'),
    path('quizzes/<int:pk>/duplicar/', views.quiz_duplicar, name='quiz_duplicar'),
    path('quizzes/<int:pk>/excluir/', views.quiz_excluir, name='quiz_excluir'),
    path('quizzes/<int:pk>/perguntas/nova/', views.pergunta_nova, name='pergunta_nova'),
    path('quizzes/<int:pk>/banco/', views.banco, name='banco'),
    path('perguntas/<int:pk>/editar/', views.pergunta_editar, name='pergunta_editar'),
    path('perguntas/<int:pk>/duplicar/', views.pergunta_duplicar, name='pergunta_duplicar'),
    path('perguntas/<int:pk>/excluir/', views.pergunta_excluir, name='pergunta_excluir'),
    path('perguntas/<int:pk>/mover/', views.pergunta_mover, name='pergunta_mover'),

    # Gestão: salas, partida ao vivo e resultados
    path('salas/', views.salas, name='salas'),
    path('salas/nova/', views.sala_nova, name='sala_nova'),
    path('salas/<str:codigo>/editar/', views.sala_editar, name='sala_editar'),
    path('salas/<str:codigo>/painel/', views.painel, name='painel'),
    path('salas/<str:codigo>/painel/estado/', views.painel_estado, name='painel_estado'),
    path('salas/<str:codigo>/painel/acao/', views.painel_acao, name='painel_acao'),
    path('salas/<str:codigo>/resultados/', views.resultados, name='resultados'),
    path('salas/<str:codigo>/resultados.xlsx', views.resultados_excel, name='resultados_excel'),

    # Relatórios
    path('relatorios/', views.relatorios_view, name='relatorios'),
    path('relatorios.xlsx', views.relatorios_excel, name='relatorios_excel'),
]
