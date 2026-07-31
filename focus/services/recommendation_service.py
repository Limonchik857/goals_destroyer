from datetime import date

from django.utils import timezone

from tasks.models import Task

from ..models import AvailableTime, EnergyLevel, FocusLevel


# Веса для расчёта баллов
# Срочность
OVERDUE_SCORE = 40
DEADLINE_TODAY_SCORE = 35
DEADLINE_TOMORROW_SCORE = 25
DEADLINE_SOON_SCORE = 10

# Приоритет
HIGH_PRIORITY_SCORE = 25
MEDIUM_PRIORITY_SCORE = 15
LOW_PRIORITY_SCORE = 5

# Соответствие энергии
ENERGY_MATCH = {
    EnergyLevel.LOW: {Task.Difficulty.EASY: 15, Task.Difficulty.MEDIUM: 5, Task.Difficulty.HARD: 0},
    EnergyLevel.MEDIUM: {Task.Difficulty.EASY: 8, Task.Difficulty.MEDIUM: 15, Task.Difficulty.HARD: 8},
    EnergyLevel.HIGH: {Task.Difficulty.EASY: 5, Task.Difficulty.MEDIUM: 10, Task.Difficulty.HARD: 15},
}

# Соответствие концентрации
FOCUS_MATCH = {
    FocusLevel.LOW: {Task.Difficulty.EASY: 10, Task.Difficulty.MEDIUM: 4, Task.Difficulty.HARD: 0},
    FocusLevel.MEDIUM: {Task.Difficulty.EASY: 8, Task.Difficulty.MEDIUM: 10, Task.Difficulty.HARD: 6},
    FocusLevel.HIGH: {Task.Difficulty.EASY: 5, Task.Difficulty.MEDIUM: 10, Task.Difficulty.HARD: 15},
}

# Соответствие времени (зависит от estimated_duration)
TIME_MATCH = {
    AvailableTime.SHORT: {
        Task.EstimatedDuration.UP_TO_15: 10,
        Task.EstimatedDuration.UP_TO_30: 8,
        Task.EstimatedDuration.UP_TO_60: 2,
        Task.EstimatedDuration.OVER_60: 0,
    },
    AvailableTime.MEDIUM: {
        Task.EstimatedDuration.UP_TO_15: 4,
        Task.EstimatedDuration.UP_TO_30: 8,
        Task.EstimatedDuration.UP_TO_60: 10,
        Task.EstimatedDuration.OVER_60: 5,
    },
    AvailableTime.LONG: {
        Task.EstimatedDuration.UP_TO_15: 3,
        Task.EstimatedDuration.UP_TO_30: 5,
        Task.EstimatedDuration.UP_TO_60: 8,
        Task.EstimatedDuration.OVER_60: 10,
    },
}


class TaskRecommendationService:

    @classmethod
    def get_recommendation(cls, user, work_session, excluded_task_ids=None):
        today = timezone.localdate()
        tasks = Task.objects.filter(
            owner=user,
            status=Task.Status.NOT_DONE,
        )

        if excluded_task_ids:
            tasks = tasks.exclude(pk__in=excluded_task_ids)

        scored = []
        for task in tasks:
            score, reasons = cls._score_task(task, work_session, today)
            scored.append({
                'task': task,
                'score': score,
                'reasons': reasons,
            })

        if not scored:
            return None

        scored.sort(key=lambda x: x['score'], reverse=True)
        best = scored[0]
        is_urgent = cls._is_urgent(best['task'], today)

        return {
            'task': best['task'],
            'reasons': best['reasons'],
            'score': best['score'],
            'is_urgent': is_urgent,
        }

    @classmethod
    def _score_task(cls, task, work_session, today):
        score = 0
        reasons = []

        # 1. Просроченность и срочность
        if task.deadline:
            days_until = (task.deadline - today).days
            if days_until < 0:
                score += OVERDUE_SCORE
                reasons.append('⚠ Просрочена')
            elif days_until == 0:
                score += DEADLINE_TODAY_SCORE
                reasons.append('✓ Дедлайн сегодня')
            elif days_until == 1:
                score += DEADLINE_TOMORROW_SCORE
                reasons.append('→ Дедлайн завтра')
            elif days_until <= 3:
                score += DEADLINE_SOON_SCORE
                reasons.append(f'→ Дедлайн через {days_until} дня')
            else:
                reasons.append(f'→ Дедлайн через {days_until} дней')

        # 2. Приоритет
        priority_map = {
            Task.Priority.HIGH: (HIGH_PRIORITY_SCORE, '✓ Высокий приоритет'),
            Task.Priority.MEDIUM: (MEDIUM_PRIORITY_SCORE, '→ Средний приоритет'),
            Task.Priority.LOW: (LOW_PRIORITY_SCORE, 'Низкий приоритет'),
        }
        pscore, preason = priority_map.get(task.priority, (0, ''))
        score += pscore
        reasons.append(preason)

        # 3. Соответствие энергии
        energy_scores = ENERGY_MATCH.get(work_session.energy, ENERGY_MATCH[EnergyLevel.MEDIUM])
        es = energy_scores.get(task.difficulty, 0)
        score += es
        if es >= 15:
            reasons.append('✓ Энергия подходит для этой задачи')
        elif es >= 8:
            reasons.append('→ Уровень энергии допустим')
        elif es == 0:
            reasons.append('✗ Задача требует больше энергии')

        # 4. Соответствие концентрации
        focus_scores = FOCUS_MATCH.get(work_session.focus, FOCUS_MATCH[FocusLevel.MEDIUM])
        fs = focus_scores.get(task.difficulty, 0)
        score += fs
        if fs >= 10:
            reasons.append('✓ Концентрация подходит')
        elif fs >= 4:
            reasons.append('→ Концентрация допустима')
        else:
            reasons.append('✗ Сейчас сложно сосредоточиться на таком уровне')

        # 5. Соответствие времени
        time_scores = TIME_MATCH.get(work_session.available_time, TIME_MATCH[AvailableTime.MEDIUM])
        ts = time_scores.get(task.estimated_duration, 0)
        score += ts
        if ts >= 8:
            reasons.append('✓ Укладывается в доступное время')
        elif ts >= 2:
            reasons.append('→ Почти укладывается в доступное время')
        else:
            reasons.append('✗ Превышает доступное время')

        return score, reasons

    @classmethod
    def _is_urgent(cls, task, today):
        if not task.deadline:
            return False
        days_until = (task.deadline - today).days
        return days_until < 0 or (days_until <= 1 and task.priority >= Task.Priority.HIGH)
