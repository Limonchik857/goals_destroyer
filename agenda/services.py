"""Логика собрания: темы, перенос между встречами, итог."""
from .models import Meeting, Topic


def active_topics(meeting):
    """Темы, которые ещё предстоит обсудить (в порядке добавления)."""
    return list(
        meeting.topics.filter(discussed=False, dropped=False).order_by("created_at")
    )


def discussed_topics(meeting):
    """Утверждённые темы — что обсудили."""
    return list(
        meeting.topics.filter(discussed=True, dropped=False).order_by("-created_at")
    )


def pending_for_carry(meeting):
    """Активные темы для переноса на следующую встречу."""
    return active_topics(meeting)


def carry_to_next(meeting):
    """Создать следующую встречу и перенести туда неутверждённые темы.

    Возвращает новую встречу; текущая помечается «разобрано».
    """
    next_meeting = Meeting.objects.create(
        owner=meeting.owner,
        title=meeting.title,
        organizer=meeting.organizer,
        phase=Meeting.Phase.COLLECT,
    )
    meeting.next_meeting = next_meeting
    meeting.phase = Meeting.Phase.DONE
    meeting.save(update_fields=["next_meeting", "phase"])
    for topic in pending_for_carry(meeting):
        Topic.objects.create(
            meeting=next_meeting,
            text=topic.text,
            author_token=topic.author_token,
        )
    return next_meeting


def meeting_summary_markdown(meeting):
    """Итог собрания как Markdown: для протокола или письма."""
    lines = [f"# {meeting.title}", ""]
    if meeting.summary:
        lines += [meeting.summary.strip(), ""]
    disc = discussed_topics(meeting)
    if disc:
        lines += ["## Обсудили", ""]
        for t in disc:
            lines.append(f"- {t.text}")
        lines.append("")
    pending = active_topics(meeting)
    if pending:
        lines += ["## Перенесено на следующую встречу", ""]
        for t in pending:
            lines.append(f"- {t.text}")
    return "\n".join(lines) + "\n"