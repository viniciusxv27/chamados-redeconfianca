from django.contrib import admin
from .models import Training, TrainingView

@admin.register(Training)
class TrainingAdmin(admin.ModelAdmin):
    list_display = ('title', 'uploaded_by', 'is_active', 'views_count', 'get_duration_display', 'created_at')
    list_filter = ('is_active', 'created_at', 'uploaded_by')
    search_fields = ('title', 'description')
    readonly_fields = ('views_count', 'created_at', 'updated_at', 'file_size')
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('title', 'description')
        }),
        ('Arquivo', {
            'fields': ('video_file', 'thumbnail', 'duration_seconds', 'file_size')
        }),
        ('Configurações', {
            'fields': ('uploaded_by', 'is_active')
        }),
        ('Estatísticas', {
            'fields': ('views_count', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

@admin.register(TrainingView)
class TrainingViewAdmin(admin.ModelAdmin):
    list_display = ('training', 'user', 'viewed_at', 'duration_watched', 'completed')
    list_filter = ('completed', 'viewed_at')
    search_fields = ('training__title', 'user__username')
    readonly_fields = ('viewed_at',)
