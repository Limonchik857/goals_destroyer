"""Эвристики «письма, требующие внимания» для страницы «Сегодня».

НЕ AI. Простой консервативный фильтр: письмо показывается, только если
оно непрочитанное, свежее и при этом либо содержит явные признаки
вопроса/призыва/срока, либо уже связано с активным проектом или задачей.

Маркеры сопоставляются в Python (lower()): SQLite LIKE не сворачивает
регистр для кириллицы, поэтому DB-фильтр по русским маркерам ненадёжен.

Задача фильтра — не перегружать страницу: лучше показать 2–3 письма,
чем превратить «Сегодня» во второй inbox.
"""
import datetime

from django.utils import timezone

from ..models import EmailMessage

# Явные маркеры просьбы/призыва/срока. Словоформы русского языка.
_ACTION_MARKERS = (
    "сможе", "пришл", "отправ", "подтверд", "сделай", "сделать",
    "нужно", "необходимо", "должен", "должна", "обязателен", "ждём",
    "ждем", "ждите", "прошу", "напомин", "до пятниц", "до понедельник",
    "до сред", "до четверг", "до вторник", "до конца", "срок", "дедлайн",
    "успеть", "жду ответа", "ответьте", "позвони", "присоедин", "посмотри",
    "проверь", "проверьте", "заполни", "заполните", "сообщи",
)

_QUESTION_MARKERS = ("?", "вопрос", "уточн")


def _has_action_marker(snippet):
    if not snippet:
        return False
    low = snippet.lower()
    return "?" in snippet or any(
        marker in low for marker in _ACTION_MARKERS
    ) or any(marker in low for marker in _QUESTION_MARKERS)


def _linked_to_active(message):
    return (
        message.projects.filter(status=0).exists()
        or message.linked_tasks.filter(status=0).exists()
        or message.tasks.filter(status=0).exists()
    )


def attention_messages(user, limit=3, max_age_days=7):
    """Недавние непрочитанные письма, требующие внимания.

    Порядок: по дате письма. Возвращает список из максимум `limit` писем.
    """
    cutoff = timezone.now() - datetime.timedelta(days=max_age_days)
    candidates = (
        EmailMessage.objects.filter(
            integration__user=user,
            integration__is_active=True,
            is_read=False,
            received_at__gte=cutoff,
        )
        .select_related("integration")
        .order_by("received_at")
    )
    ordered = []
    for message in candidates:
        if len(ordered) >= limit:
            break
        if _has_action_marker(message.snippet) or _linked_to_active(message):
            ordered.append(message)
    return ordered