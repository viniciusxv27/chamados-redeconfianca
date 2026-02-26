from django.contrib import admin
from .models import Payslip


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    list_display = ['user', 'month', 'year', 'net_pay', 'uploaded_by', 'created_at']
    list_filter = ['year', 'month']
    search_fields = ['user__first_name', 'user__last_name', 'employee_name', 'cpf']
    raw_id_fields = ['user', 'uploaded_by']
    readonly_fields = ['created_at', 'updated_at']
