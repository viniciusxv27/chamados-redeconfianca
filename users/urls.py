from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Profile URLs
    path('profile/', views.profile_view, name='profile'),
    path('profile/update/', views.update_profile_view, name='update_profile'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/update/', views.update_settings_view, name='update_settings'),
    path('help/', views.help_view, name='help'),
    path('change-password/', views.change_password_view, name='change_password'),
    
    # Admin URLs - usando 'manage' para evitar conflito com django admin
    path('manage/', views.admin_panel_view, name='admin_panel'),
    path('manage/users/', views.manage_users_view, name='manage_users'),
    path('manage/users/create/', views.create_user_view, name='create_user'),
    path('manage/users/<int:user_id>/edit/', views.edit_user_view, name='edit_user'),
    path('manage/cs/', views.manage_cs_view, name='manage_cs'),
    path('manage/cs/<int:user_id>/add/', views.add_cs_view, name='add_cs'),
    path('manage/sectors/', views.manage_sectors_view, name='manage_sectors'),
    path('manage/sectors/create/', views.create_sector_view, name='create_sector'),
    path('manage/categories/', views.manage_categories_view, name='manage_categories'),
    path('manage/categories/create/', views.create_category_view, name='create_category'),
    path('manage/webhooks/', views.manage_webhooks_view, name='manage_webhooks'),
    path('manage/webhooks/create/', views.create_webhook_view, name='create_webhook'),
    path('manage/tutorials/', views.manage_tutorials_view, name='manage_tutorials'),
    path('manage/tutorials/create/', views.create_tutorial_view, name='create_tutorial'),
    path('manage/prizes/', views.manage_prizes_view, name='manage_prizes'),
    path('manage/prizes/create/', views.create_prize_view, name='create_prize'),
    path('manage/prizes/<int:prize_id>/edit/', views.edit_prize_view, name='edit_prize'),
    path('manage/redemptions/', views.manage_redemptions_view, name='manage_redemptions'),
]
