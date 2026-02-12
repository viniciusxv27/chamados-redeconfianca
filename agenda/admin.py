from django.contrib import admin
from .models import CalendarEvent, MeetingRequest


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ['title', 'owner', 'event_type', 'start', 'end', 'all_day']
    list_filter = ['event_type', 'all_day', 'created_at']
    search_fields = ['title', 'description', 'owner__first_name', 'owner__email']
    raw_id_fields = ['owner']
    filter_horizontal = ['participants']
    date_hierarchy = 'start'


@admin.register(MeetingRequest)
class MeetingRequestAdmin(admin.ModelAdmin):
    list_display = ['title', 'requester', 'target', 'meeting_type', 'status', 'proposed_start']
    list_filter = ['status', 'meeting_type', 'created_at']
    search_fields = ['title', 'requester__first_name', 'target__first_name']
    raw_id_fields = ['requester', 'target', 'created_event']
