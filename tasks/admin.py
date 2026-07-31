from django.contrib import admin

from .models import (
    HistoryEntry,
    JournalEntry,
    Note,
    Project,
    ProjectTemplate,
    Task,
    TaskFile,
    TemplateTask,
)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "deadline", "created_at")
    list_filter = ("owner",)
    search_fields = ("name",)
    date_hierarchy = "created_at"


class TaskFileInline(admin.TabularInline):
    model = TaskFile
    extra = 0
    readonly_fields = ("uploaded_at",)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "owner",
        "project",
        "status",
        "priority",
        "recurrence",
        "deadline",
        "created_at",
    )
    list_filter = ("status", "recurrence", "project", "owner")
    search_fields = ("name", "description")
    date_hierarchy = "created_at"
    inlines = [TaskFileInline]


@admin.register(TaskFile)
class TaskFileAdmin(admin.ModelAdmin):
    list_display = ("original_name", "task", "uploaded_at")
    list_filter = ("task__owner",)
    search_fields = ("original_name", "task__name")
    date_hierarchy = "uploaded_at"


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "created_at")
    list_filter = ("owner",)
    search_fields = ("title", "text")
    date_hierarchy = "created_at"


@admin.register(HistoryEntry)
class HistoryEntryAdmin(admin.ModelAdmin):
    list_display = ("text", "owner", "created_at")
    list_filter = ("owner",)
    search_fields = ("text",)
    date_hierarchy = "created_at"
    readonly_fields = ("owner", "text", "created_at")


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("date", "text", "owner", "project", "created_at")
    list_filter = ("owner", "project")
    search_fields = ("text",)
    date_hierarchy = "date"


class TemplateTaskInline(admin.TabularInline):
    model = TemplateTask
    extra = 0


@admin.register(ProjectTemplate)
class ProjectTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    list_filter = ("owner",)
    search_fields = ("name", "description")
    date_hierarchy = "created_at"
    inlines = [TemplateTaskInline]
