from django.urls import path

from . import views

app_name = 'power_bi'

urlpatterns = [
    path('', views.power_bi_list_view, name='list'),
    path('<int:report_id>/', views.power_bi_viewer, name='viewer'),
    path('metas/', views.goals_list_view, name='goals_list'),
    path('manage/', views.manage_power_bi_view, name='manage'),
    path('manage/export-excel/', views.export_power_bi_excel_view, name='export_excel'),
    path('manage/create/', views.create_power_bi_view, name='create'),
    path('manage/<int:report_id>/edit/', views.edit_power_bi_view, name='edit'),
    path('manage/<int:report_id>/delete/', views.delete_power_bi_view, name='delete'),
    path('manage/metas/', views.manage_goals_view, name='manage_goals'),
    path('manage/metas/upload/', views.upload_goals_view, name='upload_goals'),
    path('manage/metas/<int:upload_id>/delete/', views.delete_goals_upload_view, name='delete_goals_upload'),
    path('manage/metas/<int:upload_id>/sync-mysql/', views.sync_goals_upload_to_mysql_view, name='sync_goals_upload_mysql'),
]
