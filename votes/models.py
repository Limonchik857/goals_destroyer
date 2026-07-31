"""Модели доски голосования.

Доска живёт по двум ссылкам: share_code — для участников, admin_code —
для организатора. Регистрации нет: автора карточки и голосующего
различает случайный токен в сессии, поэтому карточки анонимны —
имён на доске нет вообще.
"""
import secrets

from django.conf import settings
from django.db import models


def make_code():
    return secrets.token_urlsafe(9)


class Board(models.Model):
    class Phase(models.TextChoices):
        COLLECT = "collect", "Сбор карточек"
        VOTE = "vote", "Голосование"
        DONE = "done", "Завершено"

    # Три колонки по умолчанию: плюсы, минусы, действия.
    COLUMNS = [
        ("good", "Что было хорошо"),
        ("bad", "Что было плохо"),
        ("action", "Что меняем"),
    ]
    COLUMN_KEYS = [key for key, _ in COLUMNS]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="boards",
        verbose_name="Владелец",
        null=True, blank=True,
    )
    share_code = models.CharField(
        max_length=16, unique=True, default=make_code, editable=False
    )
    admin_code = models.CharField(
        max_length=16, unique=True, default=make_code, editable=False
    )
    title = models.CharField("Название доски", max_length=120)
    organizer = models.CharField("Имя организатора", max_length=60)
    description = models.TextField("Описание", blank=True)
    phase = models.CharField(
        "Фаза", max_length=10, choices=Phase.choices, default=Phase.COLLECT
    )
    votes_per_person = models.PositiveSmallIntegerField(
        "Точек голосования на человека", default=5
    )
    # Когда фаза заканчивается по таймеру; NULL — таймер выключен.
    timer_ends_at = models.DateTimeField("Конец фазы по таймеру", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def is_collect(self):
        return self.phase == self.Phase.COLLECT

    @property
    def is_voting(self):
        return self.phase == self.Phase.VOTE

    @property
    def is_done(self):
        return self.phase == self.Phase.DONE


class Card(models.Model):
    """Карточка в одной из колонок. Автор анонимен: хранится только
    сессионный токен, чтобы своими карточками можно было управлять."""

    board = models.ForeignKey(
        Board, on_delete=models.CASCADE, related_name="cards"
    )
    column = models.CharField("Колонка", max_length=10, choices=Board.COLUMNS)
    text = models.CharField("Текст", max_length=280)
    author_token = models.CharField(max_length=32, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.text


class Vote(models.Model):
    """Точка голосования. Одна точка от участника на карточку."""

    card = models.ForeignKey(
        Card, on_delete=models.CASCADE, related_name="votes"
    )
    voter_token = models.CharField(max_length=32, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["card", "voter_token"], name="uniq_vote_per_card"
            )
        ]

    def __str__(self):
        return f"точка → {self.card_id}"
