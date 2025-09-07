from django.urls import path
from . import views
from .test_views import test_upload_view

urlpatterns = [
    path('training/', views.training_module, name='training'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('denuncias/', views.anonymous_report, name='anonymous_report'),
    path('test-upload/', test_upload_view, name='test_upload'),  # View de teste
]
