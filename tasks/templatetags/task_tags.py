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


@register.filter
def get_item(mapping, key):
    """Значение словаря по ключу: {{ dict|get_item:key }}."""
    try:
        return mapping[key]
    except (KeyError, TypeError):
        return None


@register.filter
def has_image_extension(filename):
    """Картинка ли вложение по расширению (показываем через preview URL)."""
    from tasks.services.attachment_analysis import PREVIEW_IMAGE_EXTENSIONS

    return f".{filename.rsplit('.', 1)[-1].lower()}" in PREVIEW_IMAGE_EXTENSIONS
