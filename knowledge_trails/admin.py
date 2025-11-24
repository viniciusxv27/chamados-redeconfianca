from django.contrib import admin
from .models import (
    KnowledgeTrail, TrailModule, Lesson, QuizQuestion, QuizOption,
    TrailProgress, LessonProgress, Certificate
)


class TrailModuleInline(admin.TabularInline):
    model = TrailModule
    extra = 1
    fields = ['title', 'order', 'icon_emoji', 'map_x', 'map_y', 'is_active']


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 1
    fields = ['title', 'lesson_type', 'points', 'duration_minutes', 'order', 'is_active']


class QuizOptionInline(admin.TabularInline):
    model = QuizOption
    extra = 4
    fields = ['option_text', 'is_correct', 'order']


@admin.register(KnowledgeTrail)
class KnowledgeTrailAdmin(admin.ModelAdmin):
    list_display = ['title', 'sector', 'difficulty', 'total_points', 'is_active', 'created_at']
    list_filter = ['sector', 'difficulty', 'is_active', 'created_at']
    search_fields = ['title', 'description']
    inlines = [TrailModuleInline]
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('title', 'description', 'sector', 'is_active', 'order')
        }),
        ('Gamificação', {
            'fields': ('icon', 'color', 'difficulty', 'estimated_hours', 'total_points')
        }),
        ('Certificado', {
            'fields': ('enable_certificate', 'certificate_logo')
        }),
        ('Metadados', {
            'fields': ('created_by',),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(TrailModule)
class TrailModuleAdmin(admin.ModelAdmin):
    list_display = ['title', 'trail', 'order', 'is_active']
    list_filter = ['trail', 'is_active']
    search_fields = ['title', 'description']
    inlines = [LessonInline]
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('trail', 'title', 'description', 'order', 'is_active')
        }),
        ('Minimapa', {
            'fields': ('icon_emoji', 'map_x', 'map_y'),
            'description': 'Posição do módulo no minimapa (coordenadas de 0 a 100)'
        }),
    )


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ['title', 'module', 'lesson_type', 'points', 'duration_minutes', 'order', 'is_active']
    list_filter = ['module__trail', 'lesson_type', 'is_active']
    search_fields = ['title', 'description', 'content']
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('module', 'title', 'description', 'lesson_type', 'order', 'is_active')
        }),
        ('Conteúdo', {
            'fields': ('content', 'video_url', 'media_file')
        }),
        ('Gamificação', {
            'fields': ('points', 'duration_minutes')
        }),
    )


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display = ['question_text', 'lesson', 'points', 'order']
    list_filter = ['lesson__module__trail']
    search_fields = ['question_text']
    inlines = [QuizOptionInline]


@admin.register(TrailProgress)
class TrailProgressAdmin(admin.ModelAdmin):
    list_display = ['user', 'trail', 'status', 'total_points_earned', 'started_at', 'completed_at']
    list_filter = ['status', 'trail', 'completed_at']
    search_fields = ['user__first_name', 'user__last_name', 'user__email']
    readonly_fields = ['total_points_earned', 'started_at', 'completed_at']


@admin.register(LessonProgress)
class LessonProgressAdmin(admin.ModelAdmin):
    list_display = ['user', 'lesson', 'completed', 'quiz_score', 'completed_at']
    list_filter = ['completed', 'lesson__module__trail']
    search_fields = ['user__first_name', 'user__last_name']
    readonly_fields = ['completed_at']


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ['user', 'trail', 'certificate_code', 'issued_at']
    list_filter = ['trail', 'issued_at']
    search_fields = ['user__first_name', 'user__last_name', 'certificate_code']
    readonly_fields = ['certificate_code', 'issued_at']
