from django.contrib import admin

from .models import Participant, Poll


class ParticipantInline(admin.TabularInline):
    model = Participant
    extra = 0


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = ("title", "organizer", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("title", "organizer")
    inlines = [ParticipantInline]
