from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Board, Card, Vote
from .services import protocol_markdown, timer_seconds_left, votes_left


def make_board(**kwargs):
    return Board.objects.create(
        title=kwargs.get("title", "Голосование"),
        organizer=kwargs.get("organizer", "Иван"),
        votes_per_person=kwargs.get("votes_per_person", 5),
        owner=kwargs.get("owner"),
    )


def add_card(board, column="good", text="Карточка", token="tok"):
    return Card.objects.create(
        board=board, column=column, text=text, author_token=token
    )


class BoardCreateTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)

    def test_home_page_loads(self):
        r = self.client.get(reverse("votes:home"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Доска голосования")

    def test_create_board_redirects_to_admin(self):
        r = self.client.post(reverse("votes:home"), {
            "title": "Вопрос недели",
            "organizer": "Иван",
            "description": "",
            "votes_per_person": "5",
        })
        board = Board.objects.get()
        self.assertRedirects(r, reverse("votes:admin", kwargs={"admin_code": board.admin_code}))
        self.assertEqual(board.phase, Board.Phase.COLLECT)
        self.assertEqual(board.owner, self.user)

    def test_create_board_requires_title(self):
        r = self.client.post(reverse("votes:home"), {
            "title": "",
            "organizer": "Иван",
            "votes_per_person": "5",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Board.objects.exists())


class CardTest(TestCase):
    def setUp(self):
        self.board = make_board()
        self.url = reverse("votes:board", kwargs={"share_code": self.board.share_code})

    def test_board_page_shows_columns(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Что было хорошо")
        self.assertContains(r, "Что было плохо")
        self.assertContains(r, "Что меняем")

    def test_add_card(self):
        r = self.client.post(
            reverse("votes:card_add", kwargs={"share_code": self.board.share_code}),
            {"column": "good", "text": "Быстрый релиз"},
        )
        self.assertEqual(r.status_code, 302)
        card = Card.objects.get()
        self.assertEqual(card.text, "Быстрый релиз")
        self.assertEqual(card.column, "good")
        self.assertTrue(card.author_token)

    def test_add_card_bad_column_rejected(self):
        self.client.post(
            reverse("votes:card_add", kwargs={"share_code": self.board.share_code}),
            {"column": "hack", "text": "Текст"},
        )
        self.assertFalse(Card.objects.exists())

    def test_add_card_blocked_outside_collect(self):
        self.board.phase = Board.Phase.VOTE
        self.board.save(update_fields=["phase"])
        self.client.post(
            reverse("votes:card_add", kwargs={"share_code": self.board.share_code}),
            {"column": "good", "text": "Текст"},
        )
        self.assertFalse(Card.objects.exists())

    def test_author_can_delete_own_card(self):
        self.client.post(
            reverse("votes:card_add", kwargs={"share_code": self.board.share_code}),
            {"column": "good", "text": "Моя"},
        )
        card = Card.objects.get()
        r = self.client.post(
            reverse("votes:card_delete", kwargs={
                "share_code": self.board.share_code, "pk": card.pk,
            }),
        )
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Card.objects.exists())

    def test_stranger_cannot_delete_card(self):
        card = add_card(self.board, token="someone")
        self.client.post(
            reverse("votes:card_delete", kwargs={
                "share_code": self.board.share_code, "pk": card.pk,
            }),
        )
        self.assertTrue(Card.objects.exists())


class VoteTest(TestCase):
    def setUp(self):
        self.board = make_board(votes_per_person=2)
        self.board.phase = Board.Phase.VOTE
        self.board.save(update_fields=["phase"])
        self.card = add_card(self.board)
        self.vote_url = reverse("votes:vote_toggle", kwargs={
            "share_code": self.board.share_code, "pk": self.card.pk,
        })

    def test_vote_toggle_adds_and_removes(self):
        self.client.post(self.vote_url)
        self.assertEqual(Vote.objects.count(), 1)
        self.client.post(self.vote_url)
        self.assertEqual(Vote.objects.count(), 0)

    def test_vote_limit_enforced(self):
        card2 = add_card(self.board, column="bad", text="Вторая")
        card3 = add_card(self.board, column="action", text="Третья")
        self.client.post(self.vote_url)
        self.client.post(reverse("votes:vote_toggle", kwargs={
            "share_code": self.board.share_code, "pk": card2.pk,
        }))
        self.client.post(reverse("votes:vote_toggle", kwargs={
            "share_code": self.board.share_code, "pk": card3.pk,
        }))
        self.assertEqual(Vote.objects.count(), 2)

    def test_vote_blocked_outside_vote_phase(self):
        self.board.phase = Board.Phase.COLLECT
        self.board.save(update_fields=["phase"])
        self.client.post(self.vote_url)
        self.assertEqual(Vote.objects.count(), 0)

    def test_votes_left_counts_down(self):
        self.assertEqual(votes_left(self.board, "me"), 2)
        Vote.objects.create(card=self.card, voter_token="me")
        self.assertEqual(votes_left(self.board, "me"), 1)


class PhaseTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)
        self.board = make_board(owner=self.user)
        self.admin = reverse("votes:admin", kwargs={"admin_code": self.board.admin_code})

    def test_admin_page_requires_admin_code(self):
        r = self.client.get(reverse("votes:admin", kwargs={"admin_code": "wrong"}))
        self.assertEqual(r.status_code, 404)

    def test_set_phase(self):
        r = self.client.post(
            reverse("votes:set_phase", kwargs={"admin_code": self.board.admin_code}),
            {"phase": "vote"},
        )
        self.assertEqual(r.status_code, 302)
        self.board.refresh_from_db()
        self.assertEqual(self.board.phase, Board.Phase.VOTE)

    def test_set_phase_resets_timer(self):
        self.board.timer_ends_at = "2030-01-01T00:00:00Z"
        self.board.save(update_fields=["timer_ends_at"])
        self.client.post(
            reverse("votes:set_phase", kwargs={"admin_code": self.board.admin_code}),
            {"phase": "vote"},
        )
        self.board.refresh_from_db()
        self.assertIsNone(self.board.timer_ends_at)

    def test_done_phase_sorts_cards_by_votes(self):
        card_low = add_card(self.board, text="Мало голосов")
        card_high = add_card(self.board, text="Много голосов")
        Vote.objects.create(card=card_high, voter_token="a")
        Vote.objects.create(card=card_high, voter_token="b")
        Vote.objects.create(card=card_low, voter_token="a")
        self.board.phase = Board.Phase.DONE
        self.board.save(update_fields=["phase"])
        r = self.client.get(
            reverse("votes:board", kwargs={"share_code": self.board.share_code})
        )
        content = r.content.decode()
        self.assertLess(content.index("Много голосов"), content.index("Мало голосов"))

    def test_timer_set_and_off(self):
        url = reverse("votes:set_timer", kwargs={"admin_code": self.board.admin_code})
        self.client.post(url, {"minutes": "10"})
        self.board.refresh_from_db()
        self.assertIsNotNone(self.board.timer_ends_at)
        left = timer_seconds_left(self.board)
        self.assertGreater(left, 500)
        self.client.post(url, {"minutes": "0"})
        self.board.refresh_from_db()
        self.assertIsNone(self.board.timer_ends_at)

    def test_other_user_cannot_admin(self):
        other = User.objects.create_user(username="hacker", password="pass")
        self.client.force_login(other)
        r = self.client.get(
            reverse("votes:admin", kwargs={"admin_code": self.board.admin_code})
        )
        self.assertEqual(r.status_code, 404)


class ProtocolTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)
        self.board = make_board(owner=self.user, title="Итоги недели")

    def test_markdown_structure(self):
        card = add_card(self.board, column="good", text="Хороший релиз")
        add_card(self.board, column="action", text="Чаще говорить о блокерах")
        Vote.objects.create(card=card, voter_token="a")
        md = protocol_markdown(self.board)
        self.assertIn("# Голосование: Итоги недели", md)
        self.assertIn("## Что было хорошо", md)
        self.assertIn("- Хороший релиз — 1 т.", md)
        self.assertIn("## Что меняем", md)

    def test_protocol_downloads(self):
        r = self.client.get(
            reverse("votes:protocol_md", kwargs={"admin_code": self.board.admin_code})
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn("attachment", r["Content-Disposition"])

    def test_admin_can_delete_any_card(self):
        card = add_card(self.board, token="stranger")
        self.client.post(
            reverse("votes:admin_card_delete", kwargs={
                "admin_code": self.board.admin_code, "pk": card.pk,
            }),
        )
        self.assertFalse(Card.objects.exists())

    def test_board_delete(self):
        add_card(self.board)
        self.client.post(
            reverse("votes:board_delete", kwargs={"admin_code": self.board.admin_code})
        )
        self.assertFalse(Board.objects.exists())
        self.assertFalse(Card.objects.exists())


class RateLimitTest(TestCase):
    def setUp(self):
        self.board = make_board()
        self.url = reverse("votes:card_add", kwargs={"share_code": self.board.share_code})

    def test_rate_limit_blocks_excessive_cards(self):
        for i in range(10):
            resp = self.client.post(self.url, {
                "column": "good", "text": f"Card {i}",
            })
            self.assertIn(resp.status_code, [200, 302])
        resp = self.client.post(self.url, {
            "column": "good", "text": "Spam card",
        }, follow=True)
        self.assertContains(resp, "Слишком много запросов")
