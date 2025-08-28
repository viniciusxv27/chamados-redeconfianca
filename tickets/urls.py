from django.urls import path
from . import views

urlpatterns = [
    path('', views.tickets_list_view, name='tickets_list'),
    path('history/', views.tickets_history_view, name='tickets_history'),
    path('create/', views.ticket_create_fixed_view, name='ticket_create'),
    path('<int:ticket_id>/', views.ticket_detail_view, name='ticket_detail'),
    path('<int:ticket_id>/assume/', views.assume_ticket_view, name='assume_ticket'),
    path('<int:ticket_id>/comment/', views.add_comment_view, name='add_comment'),
    path('<int:ticket_id>/update-status/', views.update_ticket_status_view, name='update_ticket_status'),
    path('api/categories-by-sector/', views.get_categories_by_sector, name='categories_by_sector'),
    
    # Admin URLs
    path('admin/webhooks/', views.manage_webhooks_view, name='manage_webhooks'),
    path('admin/webhooks/create/', views.create_webhook_view, name='create_webhook'),
]
