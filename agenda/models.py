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


class MeetingOutcome(models.Model):
    """Итог встречи: что решили сделать после обсуждения.

    Итог живёт отдельной жизнью: к нему привязываются обычные задачи
    (Task.meeting_outcome), по которым динамически считается прогресс.
    Завершить итог можно только когда все связанные задачи выполнены;
    отмена возможна всегда, но с обязательной причиной.
    """

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "В работе"
        COMPLETED = "completed", "Выполнен"
        CANCELLED = "cancelled", "Отменён"

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="outcomes",
        verbose_name="Встреча",
    )
    project = models.ForeignKey(
        "tasks.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outcomes",
        verbose_name="Проект",
    )
    title = models.CharField("Название", max_length=255)
    description = models.TextField("Описание", blank=True)
    responsible_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Ответственный",
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    completed_at = models.DateTimeField("Время выполнения", null=True, blank=True)
    cancelled_at = models.DateTimeField("Время отмены", null=True, blank=True)
    cancellation_reason = models.TextField("Причина отмены", blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Итог встречи"
        verbose_name_plural = "Итоги встреч"

    def __str__(self):
        return self.title

    @property
    def is_in_progress(self):
        return self.status == self.Status.IN_PROGRESS

    @property
    def is_completed(self):
        return self.status == self.Status.COMPLETED

    @property
    def is_cancelled(self):
        return self.status == self.Status.CANCELLED

    @property
    def progress(self):
        """Прогресс по связанным задачам: {done, total, percent}.

        Динамический расчёт: статусы задач — единственный источник
        правды, поэтому итог всегда актуален без ручных обновлений.
        Списки аннотируют queryset (with_outcome_progress), чтобы
        не делать по два запроса на карточку.
        """
        from tasks.models import Task

        total = getattr(self, "total_tasks", None)
        if total is None:
            total = self.tasks.count()
            done = self.tasks.filter(status=Task.Status.DONE).count()
        else:
            done = self.done_tasks or 0
        percent = round(done * 100 / total) if total else 0
        return {"done": done, "total": total, "percent": percent}

    @property
    def can_complete(self):
        """Можно ли зафиксировать выполнение: есть задачи и все выполнены."""
        total = self.tasks.count()
        return bool(total) and all(t.is_done for t in self.tasks.only("status"))


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