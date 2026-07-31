"""Журнал достижений: сводки за период и экспорт в Markdown.

Сводка собирается «на автопилоте»: ручные записи пользователя плюс то,
что сайт и так знает — выполненные задачи и завершённые проекты за период.
Поэтому даже если записывать забыл, документ к ревью всё равно соберётся.
"""
import datetime

from django.utils import timezone

from ..models import JournalEntry, Project, Task

# Пресеты периодов: ключ → подпись для кнопки.
PERIODS = {
    "week": "Эта неделя",
    "last_week": "Прошлая неделя",
    "month": "30 дней",
    "halfyear": "Полгода",
}


def period_bounds(preset, today=None):
    """(date_from, date_to) для пресета. Неизвестный пресет — эта неделя."""
    today = today or timezone.localdate()
    monday = today - datetime.timedelta(days=today.weekday())
    if preset == "week":
        return monday, today
    if preset == "last_week":
        return monday - datetime.timedelta(days=7), monday - datetime.timedelta(days=1)
    if preset == "month":
        return today - datetime.timedelta(days=29), today
    if preset == "halfyear":
        return today - datetime.timedelta(days=182), today
    return monday, today


def entry_streak(user, today=None):
    """Сколько дней подряд есть хотя бы одна запись (сегодня или со вчера)."""
    today = today or timezone.localdate()
    dates = set(
        JournalEntry.objects.filter(owner=user).values_list("date", flat=True)
    )
    if not dates:
        return 0
    anchor = today if today in dates else today - datetime.timedelta(days=1)
    streak = 0
    day = anchor
    while day in dates:
        streak += 1
        day -= datetime.timedelta(days=1)
    return streak


def collect_summary(user, date_from, date_to):
    """Собрать всё для сводки за период [date_from; date_to]."""
    entries = list(
        JournalEntry.objects.filter(
            owner=user, date__gte=date_from, date__lte=date_to
        ).select_related("project")
    )
    tasks = list(
        Task.objects.filter(
            owner=user,
            status=Task.Status.DONE,
            completed_at__date__gte=date_from,
            completed_at__date__lte=date_to,
        )
        .select_related("project")
        .order_by("completed_at")
    )
    projects_done = list(
        Project.objects.filter(
            owner=user,
            status=Project.Status.COMPLETED,
            completed_at__date__gte=date_from,
            completed_at__date__lte=date_to,
        ).order_by("completed_at")
    )

    # Записи и задачи по дням — для хронологической ленты сводки.
    days = {}
    for entry in entries:
        days.setdefault(entry.date, {"entries": [], "tasks": []})["entries"].append(entry)
    for task in tasks:
        day = timezone.localtime(task.completed_at).date()
        days.setdefault(day, {"entries": [], "tasks": []})["tasks"].append(task)
    timeline = [
        {"date": day, "entries": bucket["entries"], "tasks": bucket["tasks"]}
        for day, bucket in sorted(days.items(), reverse=True)
    ]

    # Задачи по проектам — для раздела «по проектам».
    by_project = {}
    for task in tasks:
        key = task.project.name if task.project else "Без проекта"
        by_project.setdefault(key, []).append(task)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "entries": entries,
        "tasks": tasks,
        "projects_done": projects_done,
        "timeline": timeline,
        "by_project": sorted(by_project.items()),
    }


def render_markdown(summary, user):
    """Сводка как Markdown-документ: для стендапа, ревью или резюме."""
    lines = [
        f"# Что сделано: {summary['date_from']:%d.%m.%Y} — {summary['date_to']:%d.%m.%Y}",
        "",
        "## Цифры",
        f"- Выполнено задач: {len(summary['tasks'])}",
        f"- Завершено проектов: {len(summary['projects_done'])}",
        f"- Записей в журнале: {len(summary['entries'])}",
    ]

    if summary["projects_done"]:
        lines += ["", "## Завершённые проекты"]
        for project in summary["projects_done"]:
            done = timezone.localtime(project.completed_at).date()
            lines.append(f"- {project.name} ({done:%d.%m.%Y})")

    if summary["by_project"]:
        lines += ["", "## Задачи по проектам"]
        for project_name, tasks in summary["by_project"]:
            lines.append(f"### {project_name}")
            for task in tasks:
                lines.append(f"- {task.name}")

    if summary["timeline"]:
        lines += ["", "## По дням"]
        for day in summary["timeline"]:
            lines.append(f"### {day['date']:%d.%m.%Y}")
            for entry in day["entries"]:
                suffix = f" ({entry.project.name})" if entry.project else ""
                lines.append(f"- {entry.text}{suffix}")
            for task in day["tasks"]:
                lines.append(f"- выполнена задача «{task.name}»")

    return "\n".join(lines) + "\n"
