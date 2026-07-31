from django.contrib import admin

from .models import TaskWorkRecord, WorkSession


@admin.register(WorkSession)
class WorkSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'energy', 'focus', 'available_time', 'created_at')
    list_filter = ('energy', 'focus', 'created_at')


@admin.register(TaskWorkRecord)
class TaskWorkRecordAdmin(admin.ModelAdmin):
    list_display = ('user', 'task', 'result', 'started_at', 'ended_at')
    list_filter = ('result',)
