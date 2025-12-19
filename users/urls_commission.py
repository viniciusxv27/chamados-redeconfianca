from django.urls import path
from . import commission_views

urlpatterns = [
    path('', commission_views.commission_view, name='commission'),
    path('refresh/', commission_views.commission_refresh, name='commission_refresh'),
    path('export/', commission_views.export_commission_excel, name='commission_export'),
    path('api/', commission_views.commission_api, name='commission_api'),
]
