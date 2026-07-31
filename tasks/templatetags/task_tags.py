from django import template
from django.utils import timezone

register = template.Library()


@register.filter(is_safe=True)
def pluralize_ru(value, forms):
    """Согласование слова с числом для русского языка.

    Использование: {{ count }} {{ count|pluralize_ru:"задача,задачи,задач" }}
    """
    try:
        n = abs(int(value))
    except (ValueError, TypeError):
        return ""

    one, few, many = (forms.split(",") + ["", ""])[:3]

    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


@register.filter(is_safe=True)
def smart_deadline(value):
    """Умный дедлайн: сегодня / завтра / послезавтра / ДД.ММ.ГГГГ.

    Использование: {{ task.deadline|smart_deadline }}
    """
    if value is None:
        return ""

    today = timezone.localdate()
    delta = (value - today).days

    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    if delta == 2:
        return "послезавтра"
    return value.strftime("%d.%m.%Y")
