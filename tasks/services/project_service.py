"""Бизнес-логика проектов: сохранение инлайн-задач формы проекта."""


def save_task_formset(formset, *, user, project):
    """Сохранить инлайн-формсет задач проекта.

    Каждой задаче проставляются владелец и проект — во views эта
    логика не нужна. Удалённые через чекбокс «Удалить» задачи
    удаляются из базы.
    """
    formset.instance = project
    tasks = formset.save(commit=False)
    for task in tasks:
        task.owner = user
        task.project = project
        task.save()
    for obj in formset.deleted_objects:
        obj.delete()


def save_template_task_formset(formset, *, template):
    """Сохранить инлайн-формсет этапов шаблона.

    Каждому этапу проставляется шаблон. Удалённые через чекбокс
    «Удалить» этапы удаляются из базы.
    """
    formset.instance = template
    tasks = formset.save(commit=False)
    for task in tasks:
        task.template = template
        task.save()
    for obj in formset.deleted_objects:
        obj.delete()
