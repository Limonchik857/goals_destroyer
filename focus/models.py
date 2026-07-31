from django.conf import settings
from django.db import models


class EnergyLevel(models.IntegerChoices):
    LOW = 1, "Низкая"
    MEDIUM = 2, "Средняя"
    HIGH = 3, "Высокая"


class FocusLevel(models.IntegerChoices):
    LOW = 1, "Сложно сосредоточиться"
    MEDIUM = 2, "Нормальная концентрация"
    HIGH = 3, "Могу глубоко работать"


class AvailableTime(models.IntegerChoices):
    SHORT = 1, "До 30 минут"
    MEDIUM = 2, "30–90 минут"
    LONG = 3, "Больше 90 минут"


class WorkSession(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="work_sessions",
        verbose_name="Пользователь",
    )
    energy = models.PositiveSmallIntegerField(
        choices=EnergyLevel.choices,
        verbose_name="Энергия",
    )
    focus = models.PositiveSmallIntegerField(
        choices=FocusLevel.choices,
        verbose_name="Концентрация",
    )
    available_time = models.PositiveSmallIntegerField(
        choices=AvailableTime.choices,
        verbose_name="Доступное время",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Рабочая сессия"
        verbose_name_plural = "Рабочие сессии"
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"{self.user.username} — "
            f"E:{self.get_energy_display()}, "
            f"F:{self.get_focus_display()}, "
            f"T:{self.get_available_time_display()}"
        )


class TaskWorkRecord(models.Model):
    class Result(models.IntegerChoices):
        COMPLETED = 1, "Завершена"
        POSTPONED = 2, "Продолжу позже"
        CANCELLED = 3, "Отменена"

    class PostponeReason(models.IntegerChoices):
        NO_TIME = 1, "Не хватило времени"
        DISTRACTED = 2, "Меня отвлекли"
        URGENT_WORK = 3, "Появилась срочная работа"
        HARDER_THAN_EXPECTED = 4, "Задача оказалась сложнее"
        NEED_INFO = 5, "Нужна дополнительная информация"
        NOT_RELEVANT = 6, "Задача больше не актуальна"
        OTHER = 7, "Другая причина"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="task_work_records",
        verbose_name="Пользователь",
    )
    task = models.ForeignKey(
        "tasks.Task",
        on_delete=models.CASCADE,
        related_name="work_records",
        verbose_name="Задача",
    )
    work_session = models.ForeignKey(
        WorkSession,
        on_delete=models.CASCADE,
        related_name="work_records",
        verbose_name="Рабочая сессия",
    )
    started_at = models.DateTimeField(verbose_name="Начало работы")
    ended_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Конец работы"
    )
    result = models.PositiveSmallIntegerField(
        choices=Result.choices,
        null=True,
        blank=True,
        verbose_name="Результат",
    )
    postpone_reason = models.PositiveSmallIntegerField(
        choices=PostponeReason.choices,
        null=True,
        blank=True,
        verbose_name="Причина переноса",
    )
    recommendation_order = models.PositiveIntegerField(
        default=1,
        verbose_name="Номер рекомендации (1=первая)",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Запись работы"
        verbose_name_plural = "Записи работы"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.user.username} — {self.task.name} ({self.started_at})"
