from django.urls import path
from . import views

app_name = 'documentos'

urlpatterns = [
    # Área do usuário (signatário)
    path('', views.my_documents, name='my_documents'),
    path('<int:pk>/', views.document_detail, name='document_detail'),
    path('<int:pk>/pdf/', views.document_pdf, name='document_pdf'),
    path('<int:pk>/pdf-assinado/', views.document_signed_pdf, name='document_signed_pdf'),
    path('api/assinar/<int:pk>/', views.api_sign_document, name='api_sign_document'),

    # Área administrativa (SUPERADMIN)
    path('admin/', views.admin_documents, name='admin_documents'),
    path('admin/novo/', views.admin_document_create, name='admin_document_create'),
    path('admin/<int:pk>/', views.admin_document_detail, name='admin_document_detail'),
    path('admin/<int:pk>/excluir/', views.admin_delete_document, name='admin_delete_document'),
    path('admin/<int:pk>/adicionar-signatarios/', views.admin_add_signers, name='admin_add_signers'),
    path('admin/signatario/<int:pk>/remover/', views.admin_remove_signer, name='admin_remove_signer'),

    # Categorias (SUPERADMIN)
    path('admin/categorias/', views.admin_categories, name='admin_categories'),
    path('admin/categorias/<int:pk>/editar/', views.admin_category_edit, name='admin_category_edit'),
    path('admin/categorias/<int:pk>/excluir/', views.admin_category_delete, name='admin_category_delete'),
]
