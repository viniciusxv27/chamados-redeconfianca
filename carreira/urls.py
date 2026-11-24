from django.urls import path

from . import views

app_name = 'carreira'

urlpatterns = [
    path('', views.jornada, name='jornada'),
    path('certificado/', views.certificado, name='certificado'),
    path('certificado/<int:user_id>/', views.certificado, name='certificado_de'),
    path('painel/', views.painel, name='painel'),
    path('painel/liberar/', views.liberar, name='liberar'),
    path('painel/trilhas/', views.trilhas, name='trilhas'),
    path('painel/lojas/', views.lojas_aptas, name='lojas_aptas'),
    path('pessoa/<int:user_id>/', views.pessoa, name='pessoa'),
    path('pessoa/<int:user_id>/salvar/', views.pessoa_salvar, name='pessoa_salvar'),
    path('pessoa/<int:user_id>/advertencia/', views.advertencia, name='advertencia'),
    path('pedido/<int:pedido_id>/decidir/', views.decidir, name='decidir'),
]
