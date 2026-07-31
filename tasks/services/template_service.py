"""Бизнес-логика шаблонов проектов.

Views только принимают данные формы и вызывают этот сервис —
никакой длинной логики копирования в представлениях.
"""

import datetime

from django.utils import timezone

from tasks.models import Project, Task


def create_project_from_template(
    *, user, template, name=None, description=None, deadline=None, include_ids=None
):
    """Создать полностью независимый проект («запуск») по шаблону.

    Задачи именно КОПИРУЮТСЯ, а не связываются с шаблоном: последующее
    изменение шаблона никак не влияет на уже созданные проекты. Проект
    помнит свой шаблон (source_template) — на странице шаблона видна
    история запусков.

    :param name: название проекта; если пустое — берётся из шаблона.
    :param description: описание проекта; если пустое — берётся из шаблона.
    :param deadline: дедлайн проекта (у шаблона его нет).
    :param include_ids: id шаблонных задач, которые нужны в этом запуске;
        None — скопировать все. Так один шаблон обслуживает и полный
        процесс, и сокращённый (условные шаги).
    :return: созданный Project с уже скопированными задачами.
    """
    today = timezone.localdate()

    project = Project.objects.create(
        owner=user,
        name=name or template.name,
        description=description or template.description,
        deadline=deadline,
        source_template=template,
    )

    for t in template.template_tasks.all():
        if include_ids is not None and t.pk not in include_ids:
            continue
        Task.objects.create(
            owner=user,
            project=project,
            name=t.name,
            description=t.description,
            priority=t.priority,
            difficulty=t.difficulty,
            estimated_duration=t.estimated_duration,
            deadline=(
                today + datetime.timedelta(days=t.deadline_offset_days)
                if t.deadline_offset_days is not None
                else None
            ),
        )

    return project
