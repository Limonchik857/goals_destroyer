"""«Сегодня» — персональный рабочий центр текущего дня.

Страница отвечает на вопрос: «Что мне важно сделать сегодня и на что
обратить внимание?». Сервис собирает данные из существующих систем
(задачи, проекты, Focus, встречи, журнал) без создания новых сущностей.
"""
import datetime

from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from focus.models import TaskWorkRecord, WorkSession
from focus.services.recommendation_service import TaskRecommendationService

from ..models import JournalEntry, Project, Task


def plural(n, one, few, many):
    """Русская форма существительного: 1 → one, 2-4 → few, 5+ → many."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


class TodayDashboardService:
    """Агрегирует данные о текущем дне пользователя."""

    @classmethod
    def build(cls, user):
        today = timezone.localdate()
        today_tasks = cls.get_today_tasks(user, today)
        completed_today = cls.get_completed_today(user, today)
        overdue_count = cls.get_overdue_count(user, today)
        focus = cls.get_main_focus(user, today)
        events = cls.get_upcoming_events(user, today)
        attention = cls.get_attention_items(user, today, overdue_count)
        progress = cls.get_today_progress(today_tasks, completed_today)
        journal_entry = cls.get_journal_entry(user, today)
        session = cls.get_session_today(user, today)

        return {
            "today": today,
            "greeting": cls.get_greeting(),
            "day_summary": cls.get_day_summary(
                user, today_tasks, events, overdue_count
            ),
            # Главный фокус
            "focus_task": focus["task"],
            "focus_reasons": focus["reasons"],
            "focus_start_url": focus["start_url"],
            "focus_start_label": focus["start_label"],
            "focus_via_session": focus["via_session"],
            # Сегодняшние задачи
            "today_tasks": today_tasks,
            "today_tasks_count": len(today_tasks),
            "overdue_count": overdue_count,
            # Ближайшее и внимание
            "events": events,
            "attention": attention,
            # Прогресс дня
            "progress": progress,
            # Журнал
            "journal_entry": journal_entry,
            # Focus / энергия
            "focus_recent": session is not None,
            "focus_session": session,
            "focus_session_task": focus["session_task"],
        }

    # --- Сегодняшние задачи ---

    @classmethod
    def get_today_tasks(cls, user, today):
        return list(
            Task.objects.filter(
                owner=user,
                status=Task.Status.NOT_DONE,
                deadline=today,
            )
            .select_related("project")
            .order_by("-priority", "created_at")
        )

    @classmethod
    def get_completed_today(cls, user, today):
        return Task.objects.filter(
            owner=user,
            status=Task.Status.DONE,
            completed_at__date=today,
        ).count()

    @classmethod
    def get_overdue_count(cls, user, today):
        return Task.objects.filter(
            owner=user,
            status=Task.Status.NOT_DONE,
            deadline__lt=today,
        ).count()

    # --- Главный фокус ---

    @classmethod
    def get_main_focus(cls, user, today):
        """Выбрать задачу дня.

        Порядок:
        1. Активная работа в Focus — продолжить её.
        2. Сегодняшняя WorkSession — рекомендация Focus.
        3. Просроченные → ближайший дедлайн → приоритет → раньше созданная.
        """
        active = (
            TaskWorkRecord.objects.filter(
                user=user,
                result__isnull=True,
                ended_at__isnull=True,
            )
            .select_related("task")
            .first()
        )
        if active:
            return {
                "task": active.task,
                "reasons": ["Работа уже идёт — продолжите её"],
                "start_url": reverse("focus:in_progress", args=[active.pk]),
                "start_label": "Продолжить работу",
                "via_session": False,
                "session_task": None,
            }

        session = cls.get_session_today(user, today)
        if session:
            rec = TaskRecommendationService.get_recommendation(user, session)
            if rec:
                return {
                    "task": rec["task"],
                    "reasons": rec["reasons"][:3],
                    "start_url": reverse("focus:assess"),
                    "start_label": "Начать работу",
                    "via_session": True,
                    "session_task": rec["task"],
                }

        task = (
            Task.objects.filter(owner=user, status=Task.Status.NOT_DONE)
            .select_related("project")
            .order_by(F("deadline").asc(nulls_last=True), "-priority", "created_at")
            .first()
        )
        if not task:
            return {
                "task": None,
                "reasons": [],
                "start_url": "",
                "start_label": "",
                "via_session": False,
                "session_task": None,
            }

        return {
            "task": task,
            "reasons": cls._priority_reasons(task, today),
            "start_url": reverse("focus:assess"),
            "start_label": "Начать работу",
            "via_session": False,
            "session_task": None,
        }

    @classmethod
    def _priority_reasons(cls, task, today):
        """До 3 причин для алгоритма по приоритетам и дедлайнам."""
        reasons = []
        if task.deadline:
            days = (task.deadline - today).days
            if days < 0:
                reasons.append("Просрочена")
            elif days == 0:
                reasons.append("Дедлайн сегодня")
            elif days == 1:
                reasons.append("Дедлайн завтра")
        if task.priority == Task.Priority.HIGH:
            reasons.append("Высокий приоритет")
        elif task.priority == Task.Priority.MEDIUM:
            reasons.append("Средний приоритет")
        if len(reasons) < 3 and task.difficulty == Task.Difficulty.EASY:
            reasons.append("Небольшая задача — хороший старт")
        return reasons[:3]

    # --- Focus / энергия ---

    @classmethod
    def get_session_today(cls, user, today):
        return WorkSession.objects.filter(
            user=user,
            created_at__date=today,
        ).first()

    # --- Ближайшие события ---

    @classmethod
    def get_upcoming_events(cls, user, today):
        """События текущего дня: опросы встреч и активные обсуждения."""
        events = []

        from agenda.models import Meeting
        from meetings.models import Poll

        for poll in Poll.objects.filter(
            owner=user, status=Poll.Status.OPEN
        ):
            if today not in poll.day_list():
                continue
            final = poll.final_parts()
            if final and final["date"] == today:
                label = f"Назначена на {final['time']}"
            else:
                label = f"Окно: {poll.time_from}:00–{poll.time_to}:00"
            events.append({
                "kind": "meeting",
                "title": poll.title,
                "label": label,
                "url": reverse("meetings:admin", args=[poll.admin_code]),
                "external": False,
            })

        for meeting in Meeting.objects.filter(
            owner=user, phase=Meeting.Phase.COLLECT
        ):
            if not meeting.meetpoint_url:
                continue
            events.append({
                "kind": "discussion",
                "title": meeting.title,
                "label": "Сбор тем к обсуждению",
                "url": meeting.meetpoint_url,
                "external": True,
            })

        return events

    # --- Требует внимания ---

    @classmethod
    def get_attention_items(cls, user, today, overdue_count):
        items = []
        if overdue_count:
            items.append({
                "text": (
                    f"{overdue_count} {plural(overdue_count, 'задача', 'задачи', 'задач')} "
                    f"просрочен{plural(overdue_count, 'а', 'ы', 'о')}"
                ),
                "url": reverse("tasks:overdue_tasks"),
            })

        # Активный проект с дедлайном сегодня или завтра.
        for project in Project.objects.filter(
            owner=user,
            status=Project.Status.ACTIVE,
            deadline__gte=today,
        ).exclude(deadline__isnull=True).order_by("deadline")[:2]:
            days = (project.deadline - today).days
            if days > 1:
                continue
            when = "сегодня" if days == 0 else "завтра"
            items.append({
                "text": f"Проект «{project.name}» — дедлайн {when}",
                "url": reverse("tasks:project_detail", args=[project.pk]),
            })

        # Незавершённая задача с дедлайном завтра.
        for task in Task.objects.filter(
            owner=user,
            status=Task.Status.NOT_DONE,
            deadline=today + datetime.timedelta(days=1),
        ).select_related("project")[:2]:
            items.append({
                "text": f"«{task.name}» — дедлайн завтра",
                "url": reverse("tasks:task_detail", args=[task.pk]),
            })

        return items[:4]

    # --- Прогресс дня ---

    @classmethod
    def get_today_progress(cls, today_tasks, completed_today):
        total = len(today_tasks) + completed_today
        percent = round(completed_today * 100 / total) if total else 0
        return {
            "done": completed_today,
            "total": total,
            "percent": percent,
        }

    # --- Журнал ---

    @classmethod
    def get_journal_entry(cls, user, today):
        return (
            JournalEntry.objects.filter(owner=user, date=today)
            .order_by("-created_at")
            .first()
        )

    # --- Резюме дня ---

    @classmethod
    def get_greeting(cls):
        hour = timezone.localtime().hour
        if hour < 5:
            return "Доброй ночи"
        if hour < 12:
            return "Доброе утро"
        if hour < 18:
            return "Добрый день"
        return "Добрый вечер"

    @classmethod
    def get_day_summary(cls, user, today_tasks, events, overdue_count):
        """Короткое резюме дня на основе серверной бизнес-логики."""
        meetings = sum(1 for e in events if e["kind"] == "meeting")
        parts = []
        if today_tasks:
            n = len(today_tasks)
            parts.append(f"{n} {plural(n, 'задача', 'задачи', 'задач')} на сегодня")
        if meetings:
            parts.append(f"{meetings} {plural(meetings, 'встреча', 'встречи', 'встреч')}")

        if parts:
            text = "Сегодня: " + ", ".join(parts) + "."
        else:
            text = "Сегодня нет запланированных задач и встреч."

        if overdue_count:
            text += (
                f" Просрочено: {overdue_count} "
                f"{plural(overdue_count, 'задача', 'задачи', 'задач')}."
            )
        elif today_tasks and not meetings:
            # Есть задачи — добавить указание на ближайший дедлайн.
            nearest = min(
                (t.deadline for t in today_tasks if t.deadline), default=None
            )
            if nearest:
                days = (nearest - timezone.localdate()).days
                if days <= 1:
                    text += " Ближайший дедлайн — сегодня."
        return text
