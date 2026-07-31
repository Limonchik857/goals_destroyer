"""Опросы «когда всем удобно».

Организатор создаёт сетку слотов (дни × время), участники по ссылке
отмечают удобные клетки — без регистрации. Доступ разграничен двумя
случайными кодами в URL: share_code знают участники, admin_code —
только организатор.
"""
import datetime
import secrets

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


def make_code():
    return secrets.token_urlsafe(9)


class Poll(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Открыт"
        CLOSED = "closed", "Закрыт"

    class SlotStep(models.IntegerChoices):
        HALF_HOUR = 30, "30 минут"
        HOUR = 60, "1 час"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="polls",
        verbose_name="Владелец",
        null=True, blank=True,
    )
    share_code = models.CharField(
        max_length=16, unique=True, default=make_code, editable=False
    )
    admin_code = models.CharField(
        max_length=16, unique=True, default=make_code, editable=False
    )
    title = models.CharField("Название встречи", max_length=120)
    organizer = models.CharField("Ваше имя", max_length=60)
    description = models.TextField("Описание", blank=True)
    # Список дат ISO-строками: ["2026-07-27", ...]
    dates = models.JSONField(editable=False)
    time_from = models.PositiveSmallIntegerField()  # час начала окна
    time_to = models.PositiveSmallIntegerField()    # час конца окна, не включается
    slot_minutes = models.PositiveSmallIntegerField(
        choices=SlotStep.choices, default=SlotStep.HOUR
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN
    )
    # Назначенный слот: "2026-07-27T14:00" (пусто — ещё не выбран)
    final_slot = models.CharField(max_length=16, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    def day_list(self):
        return [datetime.date.fromisoformat(d) for d in self.dates]

    def time_list(self):
        """Времена начала слотов одного дня: ["09:00", "09:30", ...]."""
        times = []
        minute = self.time_from * 60
        while minute < self.time_to * 60:
            times.append(f"{minute // 60:02d}:{minute % 60:02d}")
            minute += self.slot_minutes
        return times

    def slot_keys(self):
        """Все допустимые слоты опроса: {"2026-07-27T09:00", ...}."""
        return {f"{d}T{t}" for d in self.dates for t in self.time_list()}

    def final_parts(self):
        """Дата и время назначенного слота для шаблона (или None)."""
        if not self.final_slot:
            return None
        day, time = self.final_slot.split("T")
        return {"date": datetime.date.fromisoformat(day), "time": time}


class Participant(models.Model):
    poll = models.ForeignKey(
        Poll, on_delete=models.CASCADE, related_name="participants"
    )
    name = models.CharField(max_length=60)
    # Выбранные слоты: ["2026-07-27T09:00", ...]
    slots = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                "poll", Lower("name"), name="uniq_participant_name_per_poll"
            ),
        ]

    def __str__(self):
        return f"{self.name} — {self.poll}"
