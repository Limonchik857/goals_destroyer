"""Данные для страницы статистики.

Работает поверх строк из gamification.completed_task_rows — дополнительных
запросов к БД не делает. Вся отрисовка — чистый HTML/CSS, поэтому сервис
готовит уже посчитанные проценты и уровни интенсивности.
"""
import datetime
from collections import Counter

from django.utils import timezone

from ..models import plural_days

HEATMAP_WEEKS = 52
CHART_WEEKS = 12

MONTH_SHORT = [
    "", "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]
WEEKDAY_NAMES = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def _intensity(count):
    """Уровень окраски клетки тепловой карты: 0 (пусто) … 4 (максимум)."""
    if count == 0:
        return 0
    if count == 1:
        return 1
    if count <= 3:
        return 2
    if count <= 5:
        return 3
    return 4


def day_counter(rows):
    """Счётчик «дата → сколько задач выполнено» по строкам задач."""
    return Counter(timezone.localtime(c).date() for _, _, c, _ in rows)


def heatmap(counter, today):
    """Тепловая карта за год: колонка — неделя, строка — день недели."""
    this_monday = today - datetime.timedelta(days=today.weekday())
    start = this_monday - datetime.timedelta(weeks=HEATMAP_WEEKS - 1)
    weeks = []
    previous_month = None
    for offset in range(HEATMAP_WEEKS):
        monday = start + datetime.timedelta(weeks=offset)
        label = ""
        if monday.month != previous_month:
            # Подпись месяца над первой его неделей; самая первая колонка
            # без подписи — месяц почти всегда начался раньше неё.
            if previous_month is not None:
                label = MONTH_SHORT[monday.month]
            previous_month = monday.month
        days = []
        for shift in range(7):
            day = monday + datetime.timedelta(days=shift)
            if day > today:
                days.append(None)  # будущее — пустая клетка
            else:
                count = counter.get(day, 0)
                days.append(
                    {"date": day, "count": count, "level": _intensity(count)}
                )
        weeks.append({"label": label, "days": days})
    return weeks


def weekly_chart(counter, today):
    """Столбики: выполненные задачи по неделям, последние CHART_WEEKS."""
    this_monday = today - datetime.timedelta(days=today.weekday())
    bars = []
    for offset in range(CHART_WEEKS - 1, -1, -1):
        monday = this_monday - datetime.timedelta(weeks=offset)
        count = sum(
            counter.get(monday + datetime.timedelta(days=shift), 0)
            for shift in range(7)
        )
        bars.append({
            "start": monday,
            "end": monday + datetime.timedelta(days=6),
            "count": count,
        })
    top = max((bar["count"] for bar in bars), default=0)
    for bar in bars:
        bar["height"] = round(bar["count"] * 100 / top) if top else 0
        bar["is_current"] = bar["start"] == this_monday
    return bars


def lifetime_display(rows):
    """Среднее время от создания задачи до выполнения, по-русски."""
    deltas = [c - created for _, _, c, created in rows]
    if not deltas:
        return None
    average_days = sum(d.total_seconds() for d in deltas) / len(deltas) / 86400
    if average_days < 1:
        return "меньше дня"
    days = round(average_days)
    return f"{days} {plural_days(days)}"


def overview(rows, counter, today):
    """Сводные показатели для карточек наверху страницы."""
    with_deadline = [(d, c) for _, d, c, _ in rows if d]
    on_time = sum(
        1 for d, c in with_deadline if timezone.localtime(c).date() <= d
    )
    weekday_counts = Counter(
        timezone.localtime(c).date().weekday() for _, _, c, _ in rows
    )
    this_monday = today - datetime.timedelta(days=today.weekday())
    this_week = sum(
        counter.get(this_monday + datetime.timedelta(days=i), 0)
        for i in range(7)
    )
    last_week = sum(
        counter.get(this_monday - datetime.timedelta(days=7 - i), 0)
        for i in range(7)
    )
    return {
        "total": len(rows),
        "on_time_share": (
            round(on_time * 100 / len(with_deadline)) if with_deadline else None
        ),
        "average_lifetime": lifetime_display(rows),
        "best_weekday": (
            WEEKDAY_NAMES[weekday_counts.most_common(1)[0][0]]
            if weekday_counts
            else None
        ),
        "this_week": this_week,
        "last_week": last_week,
    }
