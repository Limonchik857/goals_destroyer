"""Сборка сетки опроса: тепловая карта пересечений и лучшие слоты."""
import datetime
from collections import Counter

HEAT_LEVELS = 4


def _vote_index(participants):
    """(счётчик голосов по слоту, имена по слоту) одним проходом."""
    counts = Counter()
    names = {}
    for person in participants:
        for key in person.slots:
            counts[key] += 1
            names.setdefault(key, []).append(person.name)
    return counts, names


def poll_grid(poll, participants, own_slots=frozenset()):
    """Строки времени × колонки дней для шаблона.

    Каждая клетка знает число голосов, «уровень тепла» 0–4 для заливки,
    имена проголосовавших (для подсказки) и флаги «мой выбор» /
    «назначенный слот».
    """
    counts, names = _vote_index(participants)
    total = len(participants)
    days = poll.day_list()
    rows = []
    for time in poll.time_list():
        cells = []
        for day in days:
            key = f"{day.isoformat()}T{time}"
            count = counts.get(key, 0)
            level = 0
            if total and count:
                level = max(1, round(count * HEAT_LEVELS / total))
            cells.append({
                "key": key,
                "count": count,
                "names": ", ".join(names.get(key, [])),
                "level": level,
                "own": key in own_slots,
                "is_final": key == poll.final_slot,
            })
        rows.append({"time": time, "cells": cells})
    return {"days": days, "rows": rows, "total": total}


def best_slots(poll, participants, limit=3):
    """Слоты с максимумом голосов, в хронологическом порядке.

    Возвращает [{date, time, count, names, everyone}], где everyone —
    признак «могут все». Пустой список, если голосов ещё нет.
    """
    counts, names = _vote_index(participants)
    valid = poll.slot_keys()
    scored = {k: v for k, v in counts.items() if k in valid and v > 0}
    if not scored:
        return []
    top = max(scored.values())
    total = len(participants)
    result = []
    for key in sorted(k for k, v in scored.items() if v == top):
        day, time = key.split("T")
        result.append({
            "key": key,
            "date": datetime.date.fromisoformat(day),
            "time": time,
            "count": top,
            "total": total,
            "names": ", ".join(names[key]),
            "everyone": top == total,
        })
    return result[:limit]
