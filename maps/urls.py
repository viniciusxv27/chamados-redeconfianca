from django.urls import path

from . import views

app_name = 'maps'

urlpatterns = [
    path('', views.mapa, name='mapa'),
    path('api/posicoes/', views.api_posicoes, name='api_posicoes'),
    # Qualquer pessoa logada envia a própria posição; ver o mapa é outra coisa.
    path('api/minha-posicao/', views.api_minha_posicao, name='api_minha_posicao'),
]
