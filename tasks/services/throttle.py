import time


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
