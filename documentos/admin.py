from django.contrib import admin

from .models import DocumentCategory, Document, DocumentSignature


@admin.register(DocumentCategory)
class DocumentCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'document_count', 'created_by', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at']


class DocumentSignatureInline(admin.TabularInline):
    model = DocumentSignature
    extra = 0
    fields = ['user', 'signed_at', 'signature_ip', 'signature_hash']
    readonly_fields = ['signed_at', 'signature_ip', 'signature_hash']
    raw_id_fields = ['user']


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'total_signers', 'signed_count', 'created_by', 'created_at']
    list_filter = ['category', 'created_at']
    search_fields = ['title', 'description']
    raw_id_fields = ['created_by']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [DocumentSignatureInline]


@admin.register(DocumentSignature)
class DocumentSignatureAdmin(admin.ModelAdmin):
    list_display = ['document', 'user', 'signed_at', 'signature_ip']
    list_filter = ['signed_at']
    search_fields = ['document__title', 'user__first_name', 'user__last_name']
    raw_id_fields = ['document', 'user', 'assigned_by']
    readonly_fields = ['signed_at', 'signature_image', 'signature_ip',
                       'signature_user_agent', 'signature_hash', 'created_at', 'updated_at']
