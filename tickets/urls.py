from django.urls import path
from . import views
from . import purchase_views

urlpatterns = [
    path('', views.tickets_list_view, name='tickets_list'),
    path('export/', views.tickets_export_view, name='tickets_export'),
    path('history/', views.tickets_history_view, name='tickets_history'),
    path('create/', views.ticket_create_fixed_view, name='ticket_create'),
    path('<int:ticket_id>/', views.ticket_detail_view, name='ticket_detail'),
    path('<int:ticket_id>/delete/', views.ticket_delete_view, name='ticket_delete'),
    path('<int:ticket_id>/assume/', views.assume_ticket_view, name='assume_ticket'),
    path('<int:ticket_id>/comment/', views.add_comment_view, name='add_comment'),
    path('<int:ticket_id>/update-status/', views.update_ticket_status_view, name='update_ticket_status'),
    path('<int:ticket_id>/update-priority/', views.update_priority_view, name='ticket_update_priority'),
    path('api/categories-by-sector/', views.get_categories_by_sector, name='categories_by_sector'),
    path('api/users-by-sector/', views.get_users_by_sector, name='users_by_sector'),
    
    # Admin URLs
    path('admin/webhooks/', views.manage_webhooks_view, name='manage_webhooks'),
    path('admin/webhooks/create/', views.create_webhook_view, name='create_webhook'),
    path('admin/webhooks/<int:webhook_id>/edit/', views.edit_webhook_view, name='edit_webhook'),
    path('admin/webhooks/<int:webhook_id>/delete/', views.delete_webhook_view, name='delete_webhook'),
    
    # Purchase Order Admin URLs
    path('admin/purchase-approvers/', purchase_views.manage_purchase_approvers_view, name='manage_purchase_approvers'),
    path('admin/purchase-approvers/create/', purchase_views.create_purchase_approver_view, name='create_purchase_approver'),
    path('admin/purchase-approvers/<int:approver_id>/update/', purchase_views.update_purchase_approver_view, name='update_purchase_approver'),
    path('admin/purchase-approvers/<int:approver_id>/delete/', purchase_views.delete_purchase_approver_view, name='delete_purchase_approver'),
    path('admin/purchase-approvals/history/', purchase_views.purchase_approvals_history_view, name='purchase_approvals_history'),
    path('admin/purchase-approvals/pending/', purchase_views.pending_approvals_view, name='pending_approvals_view'),
    
    # Purchase Order API
    path('api/purchase-orders/<int:ticket_id>/approve/<int:approval_id>/', views.approve_purchase_order, name='approve_purchase_order'),
    path('api/purchase-orders/<int:ticket_id>/reject/<int:approval_id>/', views.reject_purchase_order, name='reject_purchase_order'),
    path('api/purchase-orders/pending/', views.pending_approvals, name='pending_approvals'),
    
    # User Tickets API
    path('api/users/<int:user_id>/tickets/', views.user_tickets_api, name='user_tickets_api'),
]
