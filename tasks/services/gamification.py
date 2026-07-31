"""Геймификация: очки, уровни, серии и достижения.

Всё считается на лету из выполненных задач и завершённых проектов —
отдельного хранилища очков нет. Поэтому возврат задачи в активные или
переоткрытие проекта честно отнимает начисленное, и счёт никогда
не расходится с данными.
"""
import datetime
from collections import Counter

from django.utils import timezone

from ..models import Project, Task

# --- Очки ---

BASE_POINTS = 10        # любая выполненная задача
PRIORITY_BONUS = 5      # за каждую ступень приоритета (средний +5, высокий +10)
ON_TIME_BONUS = 5       # дедлайн был и соблюдён
LATE_PENALTY = 5        # дедлайн был и сорван
PROJECT_POINTS = 40     # завершённый проект

# Пороги уровней: (очки, название). Название последнего — цель всей игры.
LEVELS = [
    (0, "Новичок"),
    (100, "Стажёр"),
    (250, "Исполнитель"),
    (500, "Специалист"),
    (900, "Профи"),
    (1500, "Мастер"),
    (2500, "Эксперт"),
    (4000, "Машина продуктивности"),
    (6000, "Разрушитель целей"),
]

EARLY_BIRD_BEFORE = datetime.time(9, 0)
NIGHT_OWL_AFTER = datetime.time(23, 0)


def completed_task_rows(user):
    """(priority, deadline, completed_at, created_at) выполненных задач.

    Один запрос; все расчёты очков и статистики идут по этим строкам.
    """
    return list(
        Task.objects.filter(
            owner=user,
            status=Task.Status.DONE,
            completed_at__isnull=False,
        ).values_list("priority", "deadline", "completed_at", "created_at")
    )


def task_points(priority, deadline, completed_at):
    """Очки за одну выполненную задачу."""
    points = BASE_POINTS + priority * PRIORITY_BONUS
    if deadline:
        if timezone.localtime(completed_at).date() <= deadline:
            points += ON_TIME_BONUS
        else:
            points -= LATE_PENALTY
    return points


def level_info(points):
    """Текущий уровень и прогресс до следующего."""
    number = 1
    for index, (threshold, _) in enumerate(LEVELS):
        if points >= threshold:
            number = index + 1
    name = LEVELS[number - 1][1]
    if number < len(LEVELS):
        floor = LEVELS[number - 1][0]
        ceiling, next_name = LEVELS[number]
        progress = round((points - floor) * 100 / (ceiling - floor))
        to_next = ceiling - points
    else:
        next_name = None
        progress = 100
        to_next = 0
    return {
        "number": number,
        "name": name,
        "next_name": next_name,
        "progress": progress,
        "to_next": to_next,
        "is_max": number == len(LEVELS),
    }


def streak_info(dates, today):
    """Серии дней с хотя бы одной выполненной задачей.

    Текущая серия не сгорает до конца дня: если вчера задача была,
    а сегодня ещё нет — серия жива, но помечена «остывающей».
    """
    if not dates:
        return {"current": 0, "best": 0, "active_today": False}
    ordered = sorted(dates)
    best = run = 1
    for prev, cur in zip(ordered, ordered[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        best = max(best, run)
    anchor = today if today in dates else today - datetime.timedelta(days=1)
    current = 0
    day = anchor
    while day in dates:
        current += 1
        day -= datetime.timedelta(days=1)
    return {"current": current, "best": best, "active_today": today in dates}


def summary(user):
    """Полная сводка геймификации для шапки, дашборда и статистики."""
    rows = completed_task_rows(user)
    projects_done = Project.objects.filter(
        owner=user, status=Project.Status.COMPLETED
    ).count()
    points = (
        sum(task_points(p, d, c) for p, d, c, _ in rows)
        + projects_done * PROJECT_POINTS
    )
    dates = {timezone.localtime(c).date() for _, _, c, _ in rows}
    return {
        "points": points,
        "level": level_info(points),
        "streak": streak_info(dates, timezone.localdate()),
        "projects_done": projects_done,
        "rows": rows,   # для переиспользования на странице статистики
        "dates": dates,
    }


def achievements(data):
    """Список достижений с состоянием «получено» и прогрессом.

    data — результат summary(). Всё выводится из него, дополнительных
    запросов нет.
    """
    rows = data["rows"]
    total = len(rows)
    per_day = Counter(timezone.localtime(c).date() for _, _, c, _ in rows)
    max_day = max(per_day.values(), default=0)
    with_deadline = [(d, c) for _, d, c, _ in rows if d]
    on_time = sum(
        1 for d, c in with_deadline if timezone.localtime(c).date() <= d
    )
    high = sum(1 for p, _, _, _ in rows if p == Task.Priority.HIGH)
    times = [timezone.localtime(c).time() for _, _, c, _ in rows]
    best_streak = data["streak"]["best"]
    projects_done = data["projects_done"]

    def item(emoji, title, description, earned, done=None, goal=None):
        entry = {
            "emoji": emoji,
            "title": title,
            "description": description,
            "earned": earned,
        }
        if goal is not None:
            entry["progress"] = f"{min(done, goal)}/{goal}"
        return entry

    return [
        item("🎯", "Первая задача", "Выполнить первую задачу", total >= 1),
        item("⚡", "Продуктивный день", "5 задач за один день",
             max_day >= 5, max_day, 5),
        item("💥", "Разгром", "10 задач за один день",
             max_day >= 10, max_day, 10),
        item("🔥", "Ровный темп", "Выполнять задачи 7 дней подряд",
             best_streak >= 7, best_streak, 7),
        item("🌋", "Железная дисциплина", "Выполнять задачи 30 дней подряд",
             best_streak >= 30, best_streak, 30),
        item("⏰", "Точно в срок", "10 задач с дедлайном закрыты вовремя",
             on_time >= 10, on_time, 10),
        item("🎖️", "Гроза приоритетов", "20 задач высокого приоритета",
             high >= 20, high, 20),
        item("💯", "Сотня", "100 выполненных задач", total >= 100, total, 100),
        item("🏁", "Финишер", "Завершить первый проект", projects_done >= 1),
        item("🏆", "Серийный финишер", "Завершить 5 проектов",
             projects_done >= 5, projects_done, 5),
        item("🌅", "Ранняя пташка", "Закрыть задачу до 9 утра",
             any(t < EARLY_BIRD_BEFORE for t in times)),
        item("🦉", "Полуночник", "Закрыть задачу после 23:00",
             any(t >= NIGHT_OWL_AFTER for t in times)),
    ]
