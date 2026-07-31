from django.contrib import admin

from .models import Board, Card, Vote


class CardInline(admin.TabularInline):
    model = Card
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("title", "organizer", "phase", "created_at")
    list_filter = ("phase",)
    search_fields = ("title", "organizer")
    date_hierarchy = "created_at"
    readonly_fields = ("share_code", "admin_code")
    inlines = [CardInline]


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("text", "board", "column", "created_at")
    list_filter = ("column", "board")
    search_fields = ("text",)
    date_hierarchy = "created_at"


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ("card", "created_at")
    date_hierarchy = "created_at"
