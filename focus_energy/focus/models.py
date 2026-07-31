from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class DailyState(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name='Пользователь',
    )
    date = models.DateField(verbose_name='Дата')
    energy = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        verbose_name='Энергия',
    )
    focus = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        verbose_name='Концентрация',
    )
    available_minutes = models.IntegerField(verbose_name='Доступное время (мин)')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Дневное состояние'
        verbose_name_plural = 'Дневные состояния'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'date'],
                name='unique_daily_state',
            )
        ]

    def __str__(self):
        return f'{self.user.username} — {self.date} (E:{self.energy} F:{self.focus})'


class FocusTask(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name='Пользователь',
    )
    title = models.CharField(max_length=200, verbose_name='Название')
    description = models.TextField(blank=True, verbose_name='Описание')
    priority = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        verbose_name='Приоритет',
    )
    estimated_minutes = models.IntegerField(verbose_name='Оценка времени (мин)')
    energy_required = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        verbose_name='Требуемая энергия',
    )
    focus_required = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        verbose_name='Требуемая концентрация',
    )
    deadline = models.DateField(null=True, blank=True, verbose_name='Срок')
    is_completed = models.BooleanField(default=False, verbose_name='Выполнена')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создана')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Завершена')

    class Meta:
        verbose_name = 'Задача'
        verbose_name_plural = 'Задачи'

    def __str__(self):
        return self.title


class RecommendationFeedback(models.Model):
    RATING_CHOICES = [
        (1, 'Не подошла'),
        (2, 'Нормально'),
        (3, 'Подошла'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name='Пользователь',
    )
    task = models.ForeignKey(
        FocusTask,
        on_delete=models.CASCADE,
        verbose_name='Задача',
    )
    daily_state = models.ForeignKey(
        DailyState,
        on_delete=models.CASCADE,
        verbose_name='Состояние',
    )
    rating = models.IntegerField(
        choices=RATING_CHOICES,
        verbose_name='Оценка',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')

    class Meta:
        verbose_name = 'Отзыв о рекомендации'
        verbose_name_plural = 'Отзывы о рекомендациях'

    def __str__(self):
        return f'{self.user.username} — {self.task.title} — {self.get_rating_display()}'
