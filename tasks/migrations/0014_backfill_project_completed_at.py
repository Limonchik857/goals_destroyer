"""Завершённые до появления completed_at проекты получают дату создания.

Точное время завершения уже не восстановить; дата создания — ближайшее
приближение, чтобы старые проекты попадали в сводки журнала достижений.
"""
from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    Project = apps.get_model("tasks", "Project")
    Project.objects.filter(status=1, completed_at__isnull=True).update(
        completed_at=F("created_at")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0013_project_completed_at"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
