"""Модели раздела «Обсуждения».

Обсуждение (Meeting) — это общий список тем для командного разговора.
    share_code — участники добавляют темы без регистрации.
    admin_code — организатор ведёт обсуждение и разбирает темы.

Фазы: сбор тем → обсуждение завершено. Пока идёт сбор, организатор
отмечает обсуждённые темы галочкой; «перенести на следующую встречу»
создаёт новое обсуждение и копирует туда неотмеченные темы.
"""
import secrets

from django.conf import settings
from django.db import models


def make_code():
    return secrets.token_urlsafe(9)


class Meeting(models.Model):
    class Phase(models.TextChoices):
        COLLECT = "collect", "Сбор тем"
        DONE = "done", "Обсуждение завершено"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agenda_meetings",
        verbose_name="Владелец",
        null=True, blank=True,
    )
    share_code = models.CharField(
        max_length=16, unique=True, default=make_code, editable=False
    )
    admin_code = models.CharField(
        max_length=16, unique=True, default=make_code, editable=False
    )
    title = models.CharField("Название обсуждения", max_length=120)
    organizer = models.CharField("Имя организатора", max_length=60)
    meetpoint_url = models.URLField("Ссылка на встречу в meet_point", blank=True)
    summary = models.TextField("Итог обсуждения", blank=True)
    phase = models.CharField(
        "Фаза", max_length=10, choices=Phase.choices, default=Phase.COLLECT
    )
    # Следующая встреча: куда «перенесли» необсуждённые темы.
    next_meeting = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="previous", verbose_name="Следующая встреча",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def is_collect(self):
        return self.phase == self.Phase.COLLECT

    @property
    def is_done(self):
        return self.phase == self.Phase.DONE


class Topic(models.Model):
    """Один пункт обсуждения. Автор анонимен: различаем по session-токену,
    чтобы каждый мог удалить только свою тему (в фазе сбора).
    """

    meeting = models.ForeignKey(
        Meeting, on_delete=models.CASCADE, related_name="topics"
    )
    text = models.CharField("Тема", max_length=200)
    author_token = models.CharField(max_length=32, editable=False)
    discussed = models.BooleanField("Обсудили", default=False)
    dropped = models.BooleanField("Снято с обсуждения", default=False)
    created_at = models.DateTimeField("Добавлено", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.text

    @property
    def active(self):
        """Видимый в списке для обсуждения."""
        return not self.discussed and not self.dropped