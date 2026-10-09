from django.urls import path

from . import views, views_piscofins, views_sangrias

app_name = 'contagem_caixa'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('loja/<int:loja_id>/', views.loja_detalhe, name='loja_detalhe'),
    path('loja/<int:loja_id>/salvar/', views.salvar_dia, name='salvar_dia'),
    path('loja/<int:loja_id>/saldo-inicial/', views.salvar_saldo_inicial,
         name='salvar_saldo_inicial'),
    path('importar/', views.importacao, name='importacao'),

    # Sangrias: cada registro entra sozinho no caixa do dia da loja.
    path('sangrias/', views_sangrias.sangrias, name='sangrias'),
    path('sangrias/registrar/', views_sangrias.sangria_registrar, name='sangria_registrar'),
    path('sangrias/<int:pk>/editar/', views_sangrias.sangria_editar, name='sangria_editar'),
    path('sangrias/<int:pk>/apagar/', views_sangrias.sangria_apagar, name='sangria_apagar'),
    path('sangrias/conferir/', views_sangrias.sangria_conferir, name='sangria_conferir'),
    path('sangrias/categorias/', views_sangrias.sangria_categorias, name='sangria_categorias'),
    path('sangrias/exportar/', views_sangrias.sangrias_exportar, name='sangrias_exportar'),

    # Contas a pagar · PIS/Cofins: documentos do mês e a planilha da competência.
    path('pis-cofins/', views_piscofins.piscofins, name='piscofins'),
    path('pis-cofins/cnpj/', views_piscofins.piscofins_cnpj, name='piscofins_cnpj'),
    path('pis-cofins/lancar/', views_piscofins.piscofins_registrar, name='piscofins_registrar'),
    path('pis-cofins/<int:pk>/editar/', views_piscofins.piscofins_editar, name='piscofins_editar'),
    path('pis-cofins/<int:pk>/apagar/', views_piscofins.piscofins_apagar, name='piscofins_apagar'),
    path('pis-cofins/planilha/', views_piscofins.piscofins_planilha, name='piscofins_planilha'),
    path('pis-cofins/pacote/', views_piscofins.piscofins_pacote, name='piscofins_pacote'),
]
