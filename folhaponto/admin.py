from django.contrib import admin

from .models import FolhaPonto


@admin.register(FolhaPonto)
class FolhaPontoAdmin(admin.ModelAdmin):
    list_display = ['user', 'month', 'year', 'total_trabalhadas', 'total_saldo',
                    'signed_at', 'uploaded_by', 'created_at']
    list_filter = ['year', 'month', 'signed_at']
    search_fields = ['user__first_name', 'user__last_name', 'employee_name', 'cpf']
    raw_id_fields = ['user', 'uploaded_by']
    readonly_fields = ['created_at', 'updated_at']
