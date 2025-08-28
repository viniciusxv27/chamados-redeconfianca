from rest_framework import serializers
from .models import Ticket, Category, TicketLog, TicketComment, Webhook
from users.serializers import UserSerializer


class CategorySerializer(serializers.ModelSerializer):
    sector_name = serializers.CharField(source='sector.name', read_only=True)
    
    class Meta:
        model = Category
        fields = [
            'id', 'name', 'sector', 'sector_name', 'webhook_url',
            'requires_approval', 'default_description', 'is_active'
        ]


class TicketLogSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    
    class Meta:
        model = TicketLog
        fields = [
            'id', 'user', 'user_name', 'old_status', 'new_status',
            'observation', 'created_at'
        ]


class TicketCommentSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    
    class Meta:
        model = TicketComment
        fields = ['id', 'user', 'user_name', 'comment', 'created_at']


class TicketSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.full_name', read_only=True)
    sector_name = serializers.CharField(source='sector.name', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    is_overdue = serializers.ReadOnlyField()
    time_remaining = serializers.ReadOnlyField()
    logs = TicketLogSerializer(many=True, read_only=True)
    comments = TicketCommentSerializer(many=True, read_only=True)
    
    class Meta:
        model = Ticket
        fields = [
            'id', 'title', 'description', 'sector', 'sector_name',
            'category', 'category_name', 'status', 'status_display',
            'priority', 'priority_display', 'solution', 'solution_time_hours',
            'due_date', 'is_overdue', 'time_remaining', 'created_by', 
            'created_by_name', 'assigned_to', 'assigned_to_name', 'created_at',
            'updated_at', 'resolved_at', 'closed_at',
            'requires_approval', 'approval_user', 'logs', 'comments'
        ]


class WebhookSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    sector_name = serializers.CharField(source='sector.name', read_only=True)
    event_display = serializers.CharField(source='get_event_display', read_only=True)
    
    class Meta:
        model = Webhook
        fields = [
            'id', 'name', 'url', 'event', 'event_display',
            'category', 'category_name', 'sector', 'sector_name',
            'is_active', 'headers', 'created_at'
        ]
