from django.contrib import admin

from .models import DailyState, FocusTask, RecommendationFeedback


@admin.register(DailyState)
class DailyStateAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'energy', 'focus', 'available_minutes')
    list_filter = ('date', 'energy', 'focus')


@admin.register(FocusTask)
class FocusTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'priority', 'is_completed', 'deadline')
    list_filter = ('is_completed', 'priority')


@admin.register(RecommendationFeedback)
class RecommendationFeedbackAdmin(admin.ModelAdmin):
    list_display = ('user', 'task', 'rating', 'created_at')
