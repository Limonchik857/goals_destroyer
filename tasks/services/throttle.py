import time

from django.core.cache import cache


def check_rate_limit(request, action_key, max_actions=10, period_seconds=60):
    """Проверить, не превысил ли пользователь лимит действий.

    Основана на сессии: хранит для каждого action_key список временных
    меток (Unix timestamp) последних действий. Если за period_seconds
    совершено больше max_actions запросов — возвращает False.

    Возвращает (allowed: bool, retry_after: int).
    """
    now = time.time()
    session_key = f"rate_{action_key}"
    timestamps = request.session.get(session_key, [])

    timestamps = [
        ts
        for ts in timestamps
        if now - ts < period_seconds
    ]

    if len(timestamps) >= max_actions:
        oldest = min(timestamps) if timestamps else now
        retry_after = int(period_seconds - (now - oldest))
        return False, max(1, retry_after)

    timestamps.append(now)
    request.session[session_key] = timestamps
    if len(timestamps) > max_actions * 2:
        request.session[session_key] = timestamps[-max_actions:]
    return True, 0


def check_ip_rate_limit(request, action_key, max_actions=5, period_seconds=300):
    """Проверить лимит по IP-адресу (для логина/регистрации).

    Использует Django cache: ключ — IP + action_key.
    По умолчанию: 5 попыток за 5 минут.

    Возвращает (allowed: bool, retry_after: int).
    """
    ip = _get_client_ip(request)
    cache_key = f"ip_rate_{action_key}_{ip}"
    data = cache.get(cache_key, {"count": 0, "first": time.time()})

    now = time.time()
    # Сброс счётчика, если прошёл весь период
    if now - data["first"] > period_seconds:
        data = {"count": 0, "first": now}

    data["count"] += 1
    cache.set(cache_key, data, timeout=period_seconds)

    if data["count"] > max_actions:
        retry_after = int(period_seconds - (now - data["first"]))
        return False, max(1, retry_after)

    return True, 0


def _get_client_ip(request):
    """Получить реальный IP клиента (за прокси/X-Forwarded-For)."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "0.0.0.0")
