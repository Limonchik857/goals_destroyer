"""Drop tables of the removed 'workspace' app."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0016_task_meeting_outcome"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "DROP TABLE IF EXISTS workspace_idea;",
                "DROP TABLE IF EXISTS workspace_observation;",
                "DROP TABLE IF EXISTS workspace_workspace;",
                "DROP TABLE IF EXISTS workspace_team_members;",
                "DROP TABLE IF EXISTS workspace_team;",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
