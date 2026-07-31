"""Задачи, выполненные до появления completed_at, получают дату создания.

Точное время выполнения уже не восстановить; дата создания — ближайшее
приближение, чтобы старые задачи учитывались в очках и статистике.
"""
from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    Task = apps.get_model("tasks", "Task")
    Task.objects.filter(status=1, completed_at__isnull=True).update(
        completed_at=F("created_at")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0009_task_completed_at"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
