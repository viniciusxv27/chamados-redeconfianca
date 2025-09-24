from django import template
from django.db.models import Avg, Count, Q
from trainings.models import TrainingView, TrainingProgress

register = template.Library()

@register.filter
def div(value, divisor):
    """Divide um valor por outro"""
    try:
        return float(value) / float(divisor) if divisor else 0
    except (ValueError, ZeroDivisionError):
        return 0

@register.filter
def mul(value, multiplier):
    """Multiplica um valor por outro"""
    try:
        return float(value) * float(multiplier)
    except ValueError:
        return 0

@register.filter
def percentage_progress(watched, total):
    """Calcula a porcentagem de progresso"""
    try:
        if not total or total == 0:
            return 0
        return min((float(watched) / float(total)) * 100, 100)
    except (ValueError, ZeroDivisionError):
        return 0

@register.filter
def get_training_progress_for_user(training, user):
    """Retorna o progresso do usuário para um treinamento específico"""
    if not user.is_authenticated:
        return None
    
    try:
        progress = TrainingProgress.objects.get(training=training, user=user)
        return progress
    except TrainingProgress.DoesNotExist:
        return None

@register.filter
def has_completed_training(training, user):
    """Verifica se o usuário completou um treinamento"""
    if not user.is_authenticated:
        return False
    
    try:
        progress = TrainingProgress.objects.get(training=training, user=user)
        return progress.is_completed
    except TrainingProgress.DoesNotExist:
        return False

@register.filter
def has_viewed_training(training, user):
    """Verifica se o usuário visualizou um treinamento"""
    if not user.is_authenticated:
        return False
    
    return TrainingView.objects.filter(training=training, user=user).exists()

@register.filter
def get_training_completion_percentage(training, user):
    """Retorna a porcentagem de conclusão do treinamento"""
    if not user.is_authenticated:
        return 0
    
    try:
        progress = TrainingProgress.objects.get(training=training, user=user)
        return progress.progress_percentage
    except TrainingProgress.DoesNotExist:
        return 0

@register.simple_tag
def get_training_stats(training):
    """Retorna estatísticas do treinamento"""
    total_views = TrainingView.objects.filter(training=training).count()
    completed = TrainingView.objects.filter(training=training, completed=True).count()
    completion_rate = (completed / total_views * 100) if total_views > 0 else 0
    
    return {
        'total_views': total_views,
        'completed': completed,
        'completion_rate': completion_rate
    }

@register.simple_tag
def get_user_training_stats(user):
    """Retorna estatísticas de treinamento do usuário"""
    if not user.is_authenticated:
        return {'total': 0, 'completed': 0, 'in_progress': 0}
    
    total = TrainingView.objects.filter(user=user).count()
    completed = TrainingView.objects.filter(user=user, completed=True).count()
    in_progress = total - completed
    
    return {
        'total': total,
        'completed': completed,
        'in_progress': in_progress
    }