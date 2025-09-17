from django.urls import path
from . import group_views

app_name = 'groups'

urlpatterns = [
    path('', group_views.group_management, name='group_management'),
    path('<int:group_id>/', group_views.group_detail, name='group_detail'),
    path('add-user/', group_views.add_user_to_group, name='add_user_to_group'),
    path('remove-user/', group_views.remove_user_from_group, name='remove_user_from_group'),
    path('bulk-assign/', group_views.bulk_assign_groups, name='bulk_assign_groups'),
]