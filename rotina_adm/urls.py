from django.urls import path
from django.views.generic import RedirectView

from . import views, views_guardiao as vg

app_name = 'rotina_adm'

urlpatterns = [
    path('', views.painel, name='painel'),
    path('checklist/', views.checklist_editar, name='checklist_editar'),
    path('checklist/<int:item_id>/marcar/', views.checklist_marcar, name='checklist_marcar'),
    # O ranking antigo dos analistas saiu em 27/11/2026: a Missão Guardião é o ranking. A URL velha leva para lá.
    path('ranking/', RedirectView.as_view(pattern_name='rotina_adm:guardiao_destaque', query_string=True)),
    path('estoque/registrar/', views.estoque_registrar, name='estoque_registrar'),
    path('acessos/', views.acessos, name='acessos'),
    # Missão Guardião (26/11/2026)
    path('guardiao/', vg.guardiao_tela, name='guardiao'),
    path('guardiao/missoes/', vg.guardiao_tela, {'aba': 'missoes'}, name='guardiao_missoes'),
    path('guardiao/lvut/', vg.guardiao_tela, {'aba': 'lvut'}, name='guardiao_lvut'),
    path('guardiao/agenda/', vg.guardiao_tela, {'aba': 'agenda'}, name='guardiao_agenda'),
    path('guardiao/destaque/', vg.guardiao_tela, {'aba': 'destaque'}, name='guardiao_destaque'),
    path('guardiao/codigo/', vg.guardiao_tela, {'aba': 'codigo'}, name='guardiao_codigo'),
    path('guardiao/lancar/', vg.guardiao_lancar, name='guardiao_lancar'),
    path('guardiao/contagem/', vg.guardiao_contagem, name='guardiao_contagem'),
    path('guardiao/contagem/<int:contagem_id>/regularizar/', vg.guardiao_regularizar, name='guardiao_regularizar'),
    path('guardiao/missao/', vg.guardiao_missao, name='guardiao_missao'),
]
