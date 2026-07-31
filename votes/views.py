import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import BoardForm, CardForm
from .models import Board, Card, Vote
from .services import (
    board_columns,
    protocol_markdown,
    timer_seconds_left,
    votes_left,
)
from tasks.services.throttle import check_rate_limit


def _session_key(board):
    return f"vote_token_{board.pk}"


def _voter_token(request, board):
    key = _session_key(board)
    token = request.session.get(key)
    if not token:
        token = secrets.token_urlsafe(12)
        request.session[key] = token
    return token


def _peek_token(request, board):
    return request.session.get(_session_key(board), "")


def _share_url(request, board):
    return request.build_absolute_uri(
        reverse("votes:board", kwargs={"share_code": board.share_code})
    )


@login_required
def home(request):
    form = BoardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        board = form.save(owner=request.user)
        messages.success(
            request,
            "Доска создана. Отправьте команде ссылку участника.",
        )
        return redirect("votes:admin", admin_code=board.admin_code)
    return render(request, "votes/home.html", {"form": form})


def board_detail(request, share_code):
    board = get_object_or_404(Board, share_code=share_code)
    token = _peek_token(request, board)
    return render(request, "votes/board.html", {
        "board": board,
        "columns": board_columns(board, token),
        "votes_left": votes_left(board, token) if token else board.votes_per_person,
        "timer_left": timer_seconds_left(board),
        "card_form": CardForm(),
        "share_url": _share_url(request, board),
    })


@require_POST
def card_add(request, share_code):
    allowed, retry_after = check_rate_limit(
        request, f"card_add_{share_code}", max_actions=10, period_seconds=60
    )
    if not allowed:
        messages.error(
            request,
            f"Слишком много запросов. Попробуйте через {retry_after} секунд.",
        )
        return redirect("votes:board", share_code=share_code)

    board = get_object_or_404(Board, share_code=share_code)
    if not board.is_collect:
        messages.error(request, "Сбор карточек закрыт.")
        return redirect("votes:board", share_code=share_code)
    column = request.POST.get("column", "")
    if column not in Board.COLUMN_KEYS:
        messages.error(request, "Выберите колонку для карточки.")
        return redirect("votes:board", share_code=share_code)
    form = CardForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Напишите текст карточки (до 280 знаков).")
        return redirect("votes:board", share_code=share_code)
    Card.objects.create(
        board=board,
        column=column,
        text=form.cleaned_data["text"].strip(),
        author_token=_voter_token(request, board),
    )
    return redirect("votes:board", share_code=share_code)


@require_POST
def card_delete(request, share_code, pk):
    board = get_object_or_404(Board, share_code=share_code)
    card = get_object_or_404(Card, board=board, pk=pk)
    if not board.is_collect:
        messages.error(request, "Удалять карточки можно только в фазе сбора.")
        return redirect("votes:board", share_code=share_code)
    if card.author_token != _peek_token(request, board):
        messages.error(request, "Можно удалить только свою карточку.")
        return redirect("votes:board", share_code=share_code)
    card.delete()
    messages.success(request, "Карточка удалена.")
    return redirect("votes:board", share_code=share_code)


@require_POST
def vote_toggle(request, share_code, pk):
    allowed, retry_after = check_rate_limit(
        request, f"vote_toggle_{share_code}", max_actions=20, period_seconds=60
    )
    if not allowed:
        messages.error(
            request,
            f"Слишком много запросов. Попробуйте через {retry_after} секунд.",
        )
        return redirect("votes:board", share_code=share_code)

    board = get_object_or_404(Board, share_code=share_code)
    card = get_object_or_404(Card, board=board, pk=pk)
    if not board.is_voting:
        messages.error(request, "Голосование сейчас закрыто.")
        return redirect("votes:board", share_code=share_code)
    token = _voter_token(request, board)
    existing = Vote.objects.filter(card=card, voter_token=token).first()
    if existing:
        existing.delete()
    else:
        if votes_left(board, token) <= 0:
            messages.error(request, "Точки закончились.")
            return redirect("votes:board", share_code=share_code)
        Vote.objects.create(card=card, voter_token=token)
    return redirect("votes:board", share_code=share_code)


@login_required
def board_admin(request, admin_code):
    board = get_object_or_404(Board, admin_code=admin_code, owner=request.user)
    token = _peek_token(request, board)
    return render(request, "votes/admin.html", {
        "board": board,
        "columns": board_columns(board, token),
        "timer_left": timer_seconds_left(board),
        "share_url": _share_url(request, board),
        "admin_url": request.build_absolute_uri(request.path),
        "protocol": protocol_markdown(board),
    })


@login_required
@require_POST
def admin_set_phase(request, admin_code):
    board = get_object_or_404(Board, admin_code=admin_code, owner=request.user)
    phase = request.POST.get("phase", "")
    if phase not in Board.Phase.values:
        messages.error(request, "Неизвестная фаза.")
        return redirect("votes:admin", admin_code=admin_code)
    board.phase = phase
    board.timer_ends_at = None
    board.save(update_fields=["phase", "timer_ends_at"])
    messages.success(request, f"Фаза: {Board.Phase(phase).label}.")
    return redirect("votes:admin", admin_code=admin_code)


@login_required
@require_POST
def admin_set_timer(request, admin_code):
    board = get_object_or_404(Board, admin_code=admin_code, owner=request.user)
    try:
        minutes = int(request.POST.get("minutes", "0"))
    except ValueError:
        minutes = 0
    if minutes <= 0:
        board.timer_ends_at = None
        messages.success(request, "Таймер выключен.")
    else:
        minutes = min(minutes, 120)
        board.timer_ends_at = timezone.now() + timezone.timedelta(minutes=minutes)
        messages.success(request, f"Таймер: {minutes} мин.")
    board.save(update_fields=["timer_ends_at"])
    return redirect("votes:admin", admin_code=admin_code)


@login_required
@require_POST
def admin_card_delete(request, admin_code, pk):
    board = get_object_or_404(Board, admin_code=admin_code, owner=request.user)
    card = get_object_or_404(Card, board=board, pk=pk)
    card.delete()
    messages.success(request, "Карточка удалена.")
    return redirect("votes:admin", admin_code=admin_code)


@login_required
@require_POST
def board_delete(request, admin_code):
    board = get_object_or_404(Board, admin_code=admin_code, owner=request.user)
    board.delete()
    messages.success(request, "Доска удалена.")
    return redirect("tasks:team_home")


@login_required
def protocol_md(request, admin_code):
    board = get_object_or_404(Board, admin_code=admin_code, owner=request.user)
    markdown = protocol_markdown(board)
    response = HttpResponse(
        markdown.encode("utf-8"), content_type="text/markdown; charset=utf-8"
    )
    response["Content-Disposition"] = 'attachment; filename="vote_result.md"'
    return response
