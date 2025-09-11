from django.contrib import admin
from .models import (
    Category, Ticket, TicketComment, TicketLog, TicketView, 
    TicketAssignment, Webhook, PurchaseOrderApprover, PurchaseOrderApproval
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'sector', 'is_active', 'requires_approval', 'default_solution_time_hours')
    list_filter = ('sector', 'is_active', 'requires_approval')
    search_fields = ('name', 'sector__name')
    ordering = ('sector', 'name')


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'status', 'priority', 'category', 'created_by', 'assigned_to', 'created_at')
    list_filter = ('status', 'priority', 'category__sector', 'category', 'created_at')
    search_fields = ('title', 'description', 'created_by__first_name', 'created_by__last_name')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)


@admin.register(TicketComment)
class TicketCommentAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'user', 'comment_type', 'created_at')
    list_filter = ('comment_type', 'created_at')
    search_fields = ('ticket__title', 'user__first_name', 'user__last_name', 'comment')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ('name', 'event', 'url', 'is_active', 'created_at')
    list_filter = ('event', 'is_active', 'created_at')
    search_fields = ('name', 'url')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)


@admin.register(PurchaseOrderApprover)
class PurchaseOrderApproverAdmin(admin.ModelAdmin):
    list_display = ('user', 'approval_order', 'max_amount', 'is_active')
    list_filter = ('is_active', 'approval_order')
    search_fields = ('user__first_name', 'user__last_name', 'user__email')
    ordering = ('approval_order',)


@admin.register(PurchaseOrderApproval)
class PurchaseOrderApprovalAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'approver', 'status', 'amount', 'approval_step', 'created_at', 'decided_at')
    list_filter = ('status', 'approval_step', 'created_at', 'decided_at')
    search_fields = ('ticket__title', 'approver__first_name', 'approver__last_name')
    readonly_fields = ('created_at', 'decided_at')
    ordering = ('-created_at',)
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('ticket', 'approver', 'status', 'amount', 'approval_step')
        }),
        ('Comentários', {
            'fields': ('comment',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'decided_at'),
            'classes': ('collapse',)
        }),
    )
