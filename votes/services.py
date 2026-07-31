"""Логика доски: колонки с карточками, голоса, таймер, протокол.

Всё считается из базы на каждый запрос — доски маленькие, а код
остаётся простым и без кэшей, которые надо инвалидировать.
"""
from django.db.models import Count
from django.utils import timezone

from .models import Board, Vote

PHASE_ORDER = [Board.Phase.COLLECT, Board.Phase.VOTE, Board.Phase.DONE]


def board_columns(board, voter_token):
    """Колонки с карточками для страницы доски.

    Каждая карточка: число точек, флаги «моя» (можно удалять в фазе
    сбора) и «я проголосовал». В завершённой фазе карточки внутри
    колонки сортируются по точкам — итог сразу читается.
    """
    cards = list(
        board.cards.annotate(dots=Count("votes")).order_by("created_at")
    )
    my_votes = set(
        Vote.objects.filter(
            card__board=board, voter_token=voter_token
        ).values_list("card_id", flat=True)
    )
    columns = []
    for key, title in Board.COLUMNS:
        column_cards = []
        for card in cards:
            if card.column != key:
                continue
            card.mine = card.author_token == voter_token
            card.voted = card.pk in my_votes
            column_cards.append(card)
        if board.is_done:
            column_cards.sort(key=lambda c: (-c.dots, c.created_at))
        columns.append({"key": key, "title": title, "cards": column_cards})
    return columns


def votes_left(board, voter_token):
    """Сколько точек участник ещё может поставить."""
    spent = Vote.objects.filter(
        card__board=board, voter_token=voter_token
    ).count()
    return max(0, board.votes_per_person - spent)


def timer_seconds_left(board):
    """Остаток таймера фазы в секундах; None — таймер выключен."""
    if not board.timer_ends_at:
        return None
    delta = (board.timer_ends_at - timezone.now()).total_seconds()
    return max(0, round(delta))


def protocol_markdown(board):
    """Итог голосования в Markdown."""
    cards = board.cards.annotate(dots=Count("votes"))
    lines = [
        f"# Голосование: {board.title}",
        "",
        f"Участников проголосовало: "
        f"{Vote.objects.filter(card__board=board).values('voter_token').distinct().count()}",
        f"Карточек: {cards.count()}",
    ]
    for key, title in Board.COLUMNS:
        lines += ["", f"## {title}"]
        column_cards = sorted(
            (c for c in cards if c.column == key),
            key=lambda c: (-c.dots, c.created_at),
        )
        if not column_cards:
            lines.append("- (пусто)")
        for card in column_cards:
            dots = f" — {card.dots} т." if card.dots else ""
            lines.append(f"- {card.text}{dots}")
    return "\n".join(lines) + "\n"
