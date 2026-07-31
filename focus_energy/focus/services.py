from datetime import date

from .models import FocusTask


def get_day_mode(energy, focus):
    if energy <= 2:
        return 'recovery'
    if energy == 3:
        return 'calm'
    if energy >= 4 and focus >= 4:
        return 'deep'
    if energy >= 4 and focus <= 3:
        return 'active'
    return 'calm'


MODE_TITLES = {
    'recovery': 'Восстановление',
    'calm': 'Спокойная работа',
    'deep': 'Глубокая работа',
    'active': 'Активный режим',
}

MODE_TEXTS = {
    'recovery': (
        'Сегодня лучше не перегружать себя. '
        'Начни с коротких и простых задач.'
    ),
    'calm': (
        'У тебя достаточно ресурсов для обычной работы. '
        'Выбери одну важную задачу и несколько небольших.'
    ),
    'deep': (
        'Сейчас хороший момент для сложной задачи, '
        'которая требует длительного внимания.'
    ),
    'active': (
        'У тебя много энергии, но концентрация ограничена. '
        'Подойдут активные задачи средней сложности и короткие действия.'
    ),
}


def get_day_mode_title(mode):
    return MODE_TITLES.get(mode, '')


def get_day_mode_text(mode):
    return MODE_TEXTS.get(mode, '')


def get_recommendations(user, daily_state):
    tasks = list(
        FocusTask.objects.filter(user=user, is_completed=False)
    )

    scored = []
    for task in tasks:
        score, reasons = _calculate_score(task, daily_state)
        scored.append({
            'task': task,
            'score': score,
            'reasons': reasons,
        })

    scored.sort(key=lambda x: x['score'], reverse=True)

    not_recommended = [
        s for s in scored
        if s['score'] < 0
        or any(r.startswith('✗') for r in s['reasons'])
    ][:2]

    return {
        'best': scored[0] if scored else None,
        'other_good': scored[1:5] if len(scored) > 1 else [],
        'not_recommended': not_recommended,
        'all_scored': scored,
    }


def _calculate_score(task, daily_state):
    score = 0
    reasons = []

    priority_score = task.priority * 10
    score += priority_score
    if task.priority >= 4:
        reasons.append('✓ высокий приоритет')
    elif task.priority <= 2:
        reasons.append('низкий приоритет')

    today = date.today()
    if task.deadline:
        days_until = (task.deadline - today).days
        if days_until < 0:
            score += 50
            reasons.append('⚠ срок просрочен')
        elif days_until == 0:
            score += 40
            reasons.append('✓ срок сегодня')
        elif days_until == 1:
            score += 25
            reasons.append('→ срок завтра')
        elif days_until <= 3:
            score += 15
            reasons.append(f'→ срок через {days_until} дня')
        else:
            score += 5
            reasons.append('→ срок позже')

    energy_diff = daily_state.energy - task.energy_required
    if energy_diff >= 0:
        score += 20
        reasons.append('✓ соответствует твоему уровню энергии')
    elif energy_diff == -1:
        score -= 10
        reasons.append('требует чуть больше энергии')
    elif energy_diff == -2:
        score -= 20
        reasons.append('требует больше энергии, чем есть сейчас')
    else:
        score -= 35
        reasons.append('✗ слишком энергозатратная задача')

    focus_diff = daily_state.focus - task.focus_required
    if focus_diff >= 0:
        score += 20
        reasons.append('✓ подходит для текущей концентрации')
    elif focus_diff == -1:
        score -= 10
        reasons.append('требует чуть больше концентрации')
    elif focus_diff == -2:
        score -= 20
        reasons.append('требует больше концентрации, чем сейчас')
    else:
        score -= 35
        reasons.append('✗ требует слишком высокой концентрации')

    time_diff = task.estimated_minutes - daily_state.available_minutes
    if time_diff <= 0:
        score += 15
        reasons.append('✓ укладывается в доступное время')
    elif time_diff <= 15:
        score -= 10
        reasons.append('слегка превышает доступное время')
    else:
        score -= 40
        reasons.append('✗ превышает доступное время')

    return score, reasons
