from django.urls import path
from . import views

urlpatterns = [
    # Marketplace público
    path('', views.marketplace_view, name='marketplace'),
    
    # Resgate de prêmios
    path('redeem/<int:prize_id>/', views.redeem_prize, name='redeem_prize'),
    
    # Histórico do usuário
    path('my-redemptions/', views.redemption_history, name='redemption_history'),
    
    # Gerenciamento (admin)
    path('manage/', views.manage_prizes, name='manage_prizes'),
    path('create/', views.create_prize, name='create_prize'),
    path('redemptions/', views.manage_redemptions, name='manage_redemptions'),
    path('redemptions/<int:redemption_id>/update-status/', views.update_redemption_status, name='update_redemption_status'),
    path('redemptions/<int:redemption_id>/cancel/', views.cancel_redemption, name='cancel_redemption'),
    
    # Descontos
    path('discounts/', views.manage_discounts, name='manage_discounts'),
    path('discounts/create/', views.create_discount, name='create_discount'),
    path('discounts/<int:discount_id>/edit/', views.edit_discount, name='edit_discount'),
    path('discounts/<int:discount_id>/delete/', views.delete_discount, name='delete_discount'),
    path('validate-discount/', views.validate_discount_code, name='validate_discount_code'),
]
