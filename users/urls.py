from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('forgot-password/', views.forgot_password_view, name='forgot_password'),
    path('reset-password/<uidb64>/<token>/', views.reset_password_view, name='reset_password'),
    path('logout/', views.logout_view, name='logout'),
    
    # Profile URLs
    path('profile/', views.profile_view, name='profile'),
    path('profile/update/', views.update_profile_view, name='update_profile'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/update/', views.update_settings_view, name='update_settings'),
    path('help/', views.help_view, name='help_tutorials'),
    path('help/tutorial/<int:tutorial_id>/', views.tutorial_detail_view, name='tutorial_detail'),
    path('change-password/', views.change_password_view, name='change_password'),
    
    # Admin URLs - usando 'manage' para evitar conflito com django admin
    path('manage/', views.admin_panel_view, name='admin_panel'),
    path('manage/users/', views.manage_users_view, name='manage_users'),
    path('manage/users/create/', views.create_user_view, name='create_user'),
    path('manage/users/<int:user_id>/edit/', views.edit_user_view, name='edit_user'),
    path('manage/users/export-excel/', views.export_users_excel, name='export_users_excel'),
    path('manage/users/import-excel/', views.import_users_excel, name='import_users_excel'),
    path('manage/cs/', views.manage_cs_view, name='manage_cs'),
    path('manage/cs/<int:user_id>/add/', views.add_cs_view, name='add_cs'),
    path('manage/cs/export-excel/', views.export_cs_excel, name='export_cs_excel'),
    path('manage/sectors/', views.manage_sectors_view, name='manage_sectors'),
    path('manage/sectors/create/', views.create_sector_view, name='create_sector'),
    path('manage/sectors/<int:sector_id>/edit/', views.edit_sector_view, name='edit_sector'),
    path('manage/sectors/<int:sector_id>/delete/', views.delete_sector_view, name='delete_sector'),
    path('manage/categories/', views.manage_categories_view, name='manage_categories'),
    path('manage/categories/create/', views.create_category_view, name='create_category'),
    path('manage/categories/<int:category_id>/edit/', views.edit_category_view, name='edit_category'),
    path('manage/categories/<int:category_id>/delete/', views.delete_category_view, name='delete_category'),
    path('manage/webhooks/', views.manage_webhooks_view, name='manage_webhooks'),
    path('manage/webhooks/create/', views.create_webhook_view, name='create_webhook'),
    path('manage/tutorials/', views.manage_tutorials_view, name='manage_tutorials'),
    path('manage/tutorials/create/', views.create_tutorial_view, name='create_tutorial'),
    path('manage/training-categories/', views.manage_training_categories_view, name='manage_training_categories'),
    path('manage/training-categories/create/', views.create_training_category_view, name='create_training_category'),
    path('manage/training-categories/<int:category_id>/edit/', views.edit_training_category_view, name='edit_training_category'),
    path('manage/prizes/', views.manage_prizes_view, name='manage_prizes'),
    path('manage/prizes/create/', views.create_prize_view, name='create_prize'),
    path('manage/prizes/<int:prize_id>/edit/', views.edit_prize_view, name='edit_prize'),
    path('manage/prizes/<int:prize_id>/toggle-status/', views.toggle_prize_status_view, name='toggle_prize_status'),
    path('manage/prizes/<int:prize_id>/redemptions/', views.prize_redemptions_view, name='prize_redemptions'),
    path('manage/redemptions/', views.manage_redemptions_view, name='manage_redemptions'),
    path('manage/groups/', views.manage_groups_view, name='manage_groups'),
    path('manage/groups/create/', views.create_group_view, name='create_group'),
    path('manage/groups/<int:group_id>/edit/', views.edit_group_view, name='edit_group'),
    path('manage/groups/<int:group_id>/delete/', views.delete_group_view, name='delete_group'),
    path('manage/prize-categories/', views.manage_prize_categories_view, name='manage_prize_categories'),
    path('manage/prize-categories/create/', views.create_prize_category_view, name='create_prize_category'),
    path('manage/prize-categories/<int:category_id>/edit/', views.edit_prize_category_view, name='edit_prize_category'),
    path('manage/prize-categories/<int:category_id>/delete/', views.delete_prize_category_view, name='delete_prize_category'),
    
    # C$ Approval System
    path('manage/cs-transactions/', views.pending_cs_transactions_view, name='pending_cs_transactions'),
    path('manage/cs-transactions/<int:transaction_id>/approve/', views.approve_cs_transaction, name='approve_cs_transaction'),
    path('manage/cs-transactions/<int:transaction_id>/reject/', views.reject_cs_transaction, name='reject_cs_transaction'),
    
    # Checklist URLs
    path('checklist/', views.checklist_dashboard_view, name='checklist_dashboard'),
    path('checklist/sector/', views.sector_checklists_view, name='sector_checklists'),
    path('checklist/<int:checklist_id>/', views.checklist_detail_view, name='checklist_detail'),
    path('checklist/item/<int:item_id>/update-status/', views.update_checklist_item_status, name='update_checklist_item_status'),
    
    # Tasks URLs
    path('tasks/', views.tasks_dashboard_view, name='tasks_dashboard'),
    path('tasks/<int:task_id>/update-status/', views.update_task_status, name='update_task_status'),
    
    # Admin Checklist & Tasks Management
    path('manage/checklists/', views.manage_checklists_view, name='manage_checklists'),
    path('manage/checklists/create-daily/', views.create_daily_checklist, name='create_daily_checklist'),
    path('manage/checklists/template/<int:template_id>/delete/', views.delete_checklist_template, name='delete_checklist_template'),
    path('manage/tasks/', views.manage_tasks_view, name='manage_tasks'),
    path('manage/tasks/create/', views.create_task_view, name='create_task'),
    path('manage/tasks/<int:task_id>/delete/', views.delete_task, name='delete_task'),
]
