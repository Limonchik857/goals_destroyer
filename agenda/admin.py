from django.contrib import admin

from .models import Meeting, MeetingOutcome, Topic


class TopicInline(admin.TabularInline):
    model = Topic
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("title", "organizer", "phase", "created_at")
    list_filter = ("phase",)
    search_fields = ("title", "organizer")
    date_hierarchy = "created_at"
    readonly_fields = ("share_code", "admin_code", "next_meeting")
    inlines = [TopicInline]


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("text", "meeting", "discussed", "dropped", "created_at")
    list_filter = ("discussed", "dropped", "meeting")
    search_fields = ("text",)
    date_hierarchy = "created_at"


@admin.register(MeetingOutcome)
class MeetingOutcomeAdmin(admin.ModelAdmin):
    list_display = ("title", "meeting", "project", "responsible_user", "status", "created_at")
    list_filter = ("status", "meeting")
    search_fields = ("title", "description", "cancellation_reason")
    readonly_fields = ("created_at", "completed_at", "cancelled_at")