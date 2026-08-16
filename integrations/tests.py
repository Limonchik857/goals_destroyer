"""Тесты интеграции Gmail: OAuth, доступ, синхронизация, задачи, проекты, Today."""
import datetime
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tasks.models import Project, Task

from .models import EmailIntegration, EmailMessage
from .services import gmail, oauth, sync

TEST_ENC_KEY = "integration-test-key"


@override_settings(TOKEN_ENCRYPTION_KEY=TEST_ENC_KEY)
class IntegrationTestCase(TestCase):
    """Общая база: пользователи и интеграция с зашифрованными токенами."""

    def setUp(self):
        self.user = User.objects.create_user("u", password="p")
        self.other = User.objects.create_user("other", password="p")

    def make_integration(self, user=None, email="user@gmail.com", active=True):
        integration = EmailIntegration.objects.create(
            user=user or self.user,
            provider="gmail",
            email=email,
            is_active=active,
        )
        integration.set_tokens(
            "access-token-1", "refresh-token-1",
            timezone.now() + timezone.timedelta(hours=1),
        )
        integration.save()
        return integration

    def make_message(self, integration, mid="msg-1", subject="Отчёт",
                     snippet="Пришлите отчёт до пятницы?", days_ago=0):
        return EmailMessage.objects.create(
            integration=integration,
            provider_message_id=mid,
            thread_id=f"thread-{mid}",
            sender_name="Анна",
            sender_email="anna@example.com",
            subject=subject,
            snippet=snippet,
            received_at=timezone.now() - timezone.timedelta(days=days_ago),
            gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{mid}",
            is_read=False,
        )


class OAuthCallbackTest(IntegrationTestCase):
    """Подключение через Google OAuth."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = reverse("integrations:gmail_callback")
        self.state = "state-123"
        session = self.client.session
        session["gmail_oauth_state"] = self.state
        session.save()

    @mock.patch("integrations.services.oauth.exchange_code")
    @mock.patch("integrations.services.gmail.get_profile")
    def test_callback_creates_integration(self, get_profile, exchange_code):
        get_profile.return_value = {"emailAddress": "user@gmail.com"}
        exchange_code.return_value = {
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        }
        r = self.client.get(self.url, {"code": "c1", "state": self.state})
        self.assertRedirects(r, reverse("integrations:gmail_detail"))
        integration = EmailIntegration.objects.get(user=self.user)
        self.assertEqual(integration.email, "user@gmail.com")
        self.assertEqual(integration.decrypt_access_token(), "at")
        self.assertEqual(integration.decrypt_refresh_token(), "rt")
        self.assertTrue(integration.is_active)

    @mock.patch("integrations.services.oauth.exchange_code")
    @mock.patch("integrations.services.gmail.get_profile")
    def test_reconnect_updates_not_duplicates(self, get_profile, exchange_code):
        self.make_integration(email="user@gmail.com")
        get_profile.return_value = {"emailAddress": "user@gmail.com"}
        exchange_code.return_value = {
            "access_token": "new-at", "refresh_token": "new-rt",
            "expires_in": 3600,
        }
        r = self.client.get(self.url, {"code": "c2", "state": self.state})
        self.assertEqual(
            EmailIntegration.objects.filter(
                user=self.user, email="user@gmail.com"
            ).count(),
            1,
        )
        integration = EmailIntegration.objects.get(user=self.user)
        self.assertEqual(integration.decrypt_access_token(), "new-at")

    def test_cancel_does_not_create(self):
        r = self.client.get(self.url, {"error": "access_denied", "state": self.state})
        self.assertRedirects(r, reverse("integrations:gmail_detail"))
        self.assertFalse(EmailIntegration.objects.exists())

    def test_wrong_state_rejected(self):
        r = self.client.get(self.url, {"code": "c1", "state": "wrong"})
        self.assertRedirects(r, reverse("integrations:gmail_detail"))
        self.assertFalse(EmailIntegration.objects.exists())

    def test_missing_code_rejected(self):
        r = self.client.get(self.url, {"state": self.state})
        self.assertRedirects(r, reverse("integrations:gmail_detail"))
        self.assertFalse(EmailIntegration.objects.exists())

    @mock.patch("integrations.services.oauth.exchange_code")
    @mock.patch("integrations.services.gmail.get_profile")
    def test_state_consumed_after_use(self, get_profile, exchange_code):
        get_profile.return_value = {"emailAddress": "user@gmail.com"}
        exchange_code.return_value = {
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        }
        self.client.get(self.url, {"code": "c1", "state": self.state})
        self.assertNotIn("gmail_oauth_state", self.client.session)

    @mock.patch("integrations.services.oauth.exchange_code",
                side_effect=oauth.OAuthError("Google вернул ошибку."))
    def test_oauth_error_shows_message(self, exchange_code):
        r = self.client.get(self.url, {"code": "bad", "state": self.state})
        self.assertRedirects(r, reverse("integrations:gmail_detail"))
        self.assertFalse(EmailIntegration.objects.exists())


class IntegrationAccessTest(IntegrationTestCase):
    """Права: своя интеграция видна, чужая — нет."""

    def setUp(self):
        super().setUp()
        self.integration = self.make_integration()
        self.other_integration = self.make_integration(
            user=self.other, email="other@gmail.com"
        )

    def test_settings_page_shows_own_only(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("integrations:settings"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "user@gmail.com")
        self.assertNotContains(r, "other@gmail.com")

    def test_gmail_detail_requires_login(self):
        r = self.client.get(reverse("integrations:gmail_detail"))
        self.assertRedirects(r, f"{reverse('tasks:login')}?next=" + reverse(
            "integrations:gmail_detail"
        ))

    def test_other_user_cannot_see_my_emails(self):
        self.client.force_login(self.other)
        message = self.make_message(self.integration, mid="mine-1")
        r = self.client.get(
            reverse("integrations:email_detail", kwargs={"pk": message.pk})
        )
        self.assertEqual(r.status_code, 404)

    def test_disconnect_keeps_tasks(self):
        self.client.force_login(self.user)
        message = self.make_message(self.integration, mid="d-1")
        task = Task.objects.create(
            owner=self.user, name="Из письма", source_email=message
        )
        r = self.client.post(
            reverse("integrations:gmail_disconnect"),
            {"confirm": "on"},
        )
        self.assertRedirects(r, reverse("integrations:gmail_detail"))
        integration = EmailIntegration.objects.get(pk=self.integration.pk)
        self.assertFalse(integration.is_active)
        self.assertEqual(integration.encrypted_access_token, "")
        self.assertEqual(integration.encrypted_refresh_token, "")
        task.refresh_from_db()
        self.assertIsNone(task.source_email)
        self.assertEqual(Task.objects.filter(pk=task.pk).count(), 1)

    def test_disconnect_requires_confirmation(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("integrations:gmail_disconnect"), {})
        self.assertRedirects(r, reverse("integrations:gmail_detail"))
        self.assertTrue(
            EmailIntegration.objects.get(pk=self.integration.pk).is_active
        )

    def test_sync_requires_post(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("integrations:gmail_sync"))
        self.assertEqual(r.status_code, 405)


class EmailSyncTest(IntegrationTestCase):
    """Синхронизация сохраняет письма и не создаёт дубликатов."""

    def setUp(self):
        super().setUp()
        self.integration = self.make_integration()

    @mock.patch("integrations.services.gmail.list_message_ids")
    @mock.patch("integrations.services.gmail.get_message_metadata")
    def test_sync_saves_new_messages(self, get_meta, list_ids):
        list_ids.return_value = ["m1", "m2"]
        get_meta.side_effect = [
            {"thread_id": "t1", "sender_name": "Анна", "sender_email": "a@x.ru",
             "subject": "Отчёт", "snippet": "Скиньте отчёт",
             "received_at": timezone.now(), "is_read": False,
             "gmail_url": "https://mail.google.com/mail/u/0/#inbox/m1"},
            {"thread_id": "t2", "sender_name": "", "sender_email": "b@x.ru",
             "subject": "Макет", "snippet": "Подтвердите макет",
             "received_at": timezone.now(), "is_read": True,
             "gmail_url": "https://mail.google.com/mail/u/0/#inbox/m2"},
        ]
        created = sync.sync_messages(self.integration)
        self.assertEqual(created, 2)
        self.assertEqual(EmailMessage.objects.count(), 2)
        self.assertIsNotNone(self.integration.last_sync_at)
        email = EmailMessage.objects.get(provider_message_id="m1")
        self.assertEqual(email.sender_email, "a@x.ru")

    @mock.patch("integrations.services.gmail.list_message_ids")
    @mock.patch("integrations.services.gmail.get_message_metadata")
    def test_sync_does_not_duplicate(self, get_meta, list_ids):
        self.make_message(self.integration, mid="m1")
        list_ids.return_value = ["m1"]
        get_meta.return_value = {
            "thread_id": "t1", "sender_name": "Анна",
            "sender_email": "a@x.ru", "subject": "Обновлено",
            "snippet": "s", "received_at": timezone.now(),
            "is_read": True, "gmail_url": "u",
        }
        created = sync.sync_messages(self.integration)
        self.assertEqual(created, 0)
        self.assertEqual(EmailMessage.objects.count(), 1)
        email = EmailMessage.objects.get(provider_message_id="m1")
        self.assertEqual(email.subject, "Обновлено")

    def test_sync_inactive_integration_rejected(self):
        self.integration.is_active = False
        self.integration.save()
        with self.assertRaises(sync.SyncError):
            sync.sync_messages(self.integration)


class TaskFromEmailTest(IntegrationTestCase):
    """Письмо → задача: предзаполнение и сохранение источника."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.integration = self.make_integration()
        self.message = self.make_message(self.integration, mid="t-1")

    def _post_task(self, email_id=None, **extra):
        data = {
            "name": "Новая задача",
            "description": "описание",
            "deadline": timezone.localdate().isoformat(),
            "difficulty": Task.Difficulty.MEDIUM,
            "estimated_duration": Task.EstimatedDuration.UP_TO_30,
            "priority": Task.Priority.MEDIUM,
            "recurrence": Task.Recurrence.NONE,
        }
        data.update(extra)
        url = reverse("tasks:task_create")
        if email_id:
            url += f"?email={email_id}"
        return self.client.post(url, data)

    def test_create_form_prefilled_from_email(self):
        r = self.client.get(
            reverse("tasks:task_create") + f"?email={self.message.pk}"
        )
        self.assertContains(r, "Отчёт")
        self.assertContains(r, "anna@example.com")

    def test_task_from_email_saves_source(self):
        r = self._post_task(email_id=self.message.pk)
        self.assertEqual(r.status_code, 302)
        task = Task.objects.get(owner=self.user, name="Новая задача")
        self.assertEqual(task.source_email, self.message)

    def test_foreign_email_not_used_as_source(self):
        foreign = self.make_message(
            self.make_integration(user=self.other, email="other@gmail.com"),
            mid="foreign-1",
        )
        r = self._post_task(email_id=foreign.pk)
        self.assertEqual(r.status_code, 302)
        task = Task.objects.get(owner=self.user, name="Новая задача")
        self.assertIsNone(task.source_email)

    def test_regular_task_without_email(self):
        r = self._post_task()
        self.assertEqual(r.status_code, 302)
        task = Task.objects.get(owner=self.user, name="Новая задача")
        self.assertIsNone(task.source_email)

    def test_task_page_shows_source_block(self):
        task = Task.objects.create(
            owner=self.user, name="Из письма", source_email=self.message
        )
        r = self.client.get(reverse("tasks:task_detail", kwargs={"pk": task.pk}))
        self.assertContains(r, "Источник задачи")
        self.assertContains(r, "anna@example.com")
        self.assertContains(r, "Отчёт")

    def test_regular_task_has_no_source_block(self):
        task = Task.objects.create(owner=self.user, name="Обычная")
        r = self.client.get(reverse("tasks:task_detail", kwargs={"pk": task.pk}))
        self.assertNotContains(r, "Источник задачи")


class ProjectEmailTest(IntegrationTestCase):
    """Письмо → проект и отображение писем в проекте."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.integration = self.make_integration()
        self.message = self.make_message(self.integration, mid="p-1")
        self.project = Project.objects.create(
            owner=self.user, name="Интернет-магазин",
            description="desc", deadline=timezone.localdate(),
        )

    @mock.patch("integrations.services.gmail.get_message_full")
    def test_link_email_to_project(self, get_full):
        get_full.return_value = {
            "snippet": "Кратко", "body_text": "Текст", "html_text": "",
        }
        r = self.client.post(
            reverse("integrations:email_link_project",
                    kwargs={"pk": self.message.pk}),
            {"project": self.project.pk},
        )
        self.assertRedirects(
            r, reverse("integrations:email_detail", kwargs={"pk": self.message.pk})
        )
        self.assertEqual(self.message.projects.count(), 1)

    @mock.patch("integrations.services.gmail.get_message_full")
    def test_foreign_project_rejected(self, get_full):
        get_full.return_value = {
            "snippet": "Кратко", "body_text": "Текст", "html_text": "",
        }
        foreign_project = Project.objects.create(
            owner=self.other, name="Чужой", description="d",
            deadline=timezone.localdate(),
        )
        r = self.client.post(
            reverse("integrations:email_link_project",
                    kwargs={"pk": self.message.pk}),
            {"project": foreign_project.pk},
        )
        self.assertRedirects(
            r, reverse("integrations:email_detail", kwargs={"pk": self.message.pk})
        )
        self.assertEqual(self.message.projects.count(), 0)

    def test_foreign_email_cannot_be_linked(self):
        foreign = self.make_message(
            self.make_integration(user=self.other, email="other@gmail.com"),
            mid="foreign-2",
        )
        r = self.client.post(
            reverse("integrations:email_link_project",
                    kwargs={"pk": foreign.pk}),
            {"project": self.project.pk},
        )
        self.assertEqual(r.status_code, 404)

    def test_project_page_shows_emails(self):
        self.message.projects.add(self.project)
        r = self.client.get(
            reverse("tasks:project_detail", kwargs={"pk": self.project.pk})
        )
        self.assertContains(r, "Связанные письма")
        self.assertContains(r, "Отчёт")
        self.assertContains(r, "Все письма")

    def test_project_email_filter_list(self):
        self.message.projects.add(self.project)
        other = self.make_message(self.integration, mid="p-2",
                                  subject="Другое")
        r = self.client.get(
            reverse("integrations:email_list") + f"?project={self.project.pk}"
        )
        self.assertContains(r, "Отчёт")
        self.assertNotContains(r, "Другое")

    @mock.patch("integrations.services.gmail.get_message_full")
    def test_link_email_to_task_and_unlink(self, get_full):
        get_full.return_value = {
            "snippet": "Кратко", "body_text": "Текст", "html_text": "",
        }
        task = Task.objects.create(owner=self.user, name="Существующая")
        r = self.client.post(
            reverse("integrations:email_link_task",
                    kwargs={"pk": self.message.pk}),
            {"task": task.pk},
        )
        self.assertRedirects(
            r, reverse("integrations:email_detail", kwargs={"pk": self.message.pk})
        )
        self.assertEqual(self.message.linked_tasks.count(), 1)
        r = self.client.post(
            reverse("integrations:email_unlink_task",
                    kwargs={"pk": self.message.pk, "task_pk": task.pk}),
        )
        self.assertRedirects(
            r, reverse("integrations:email_detail", kwargs={"pk": self.message.pk})
        )
        self.assertEqual(self.message.linked_tasks.count(), 0)


class TodayAttentionEmailsTest(IntegrationTestCase):
    """Блок «Письма, требующие внимания» на странице «Сегодня»."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.integration = self.make_integration()

    def _home(self):
        return self.client.get(reverse("tasks:home"))

    def test_attention_email_shown(self):
        message = self.make_message(self.integration, mid="a-1")
        r = self._home()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Письма, требующие внимания")
        self.assertContains(r, "Пришлите отчёт до пятницы")
        self.assertEqual(r.context["attention_emails_count"], 1)
        self.assertEqual(r.context["attention_emails"][0], message)

    def test_read_email_not_shown(self):
        message = self.make_message(self.integration, mid="a-2")
        message.is_read = True
        message.save()
        r = self._home()
        self.assertNotContains(r, "Письма, требующие внимания")

    def test_old_email_not_shown(self):
        self.make_message(self.integration, mid="a-3", days_ago=30)
        r = self._home()
        self.assertNotContains(r, "Письма, требующие внимания")

    def test_count_correct(self):
        self.make_message(self.integration, mid="c-1")
        self.make_message(self.integration, mid="c-2",
                          subject="Второе", snippet="Подтвердите, пожалуйста")
        self.make_message(self.integration, mid="c-3",
                          subject="Третье", snippet="Жду ответа")
        self.make_message(self.integration, mid="c-4",
                          subject="Четвёртое", snippet="Жду ответа")
        r = self._home()
        self.assertEqual(r.context["attention_emails_count"], 3)

    def test_email_linked_to_project_shown(self):
        message = self.make_message(
            self.integration, mid="l-1", snippet="Без маркеров"
        )
        project = Project.objects.create(
            owner=self.user, name="Проект", description="d",
            deadline=timezone.localdate(),
        )
        message.projects.add(project)
        r = self._home()
        self.assertContains(r, "Без маркеров")

    def test_email_linked_to_task_shown(self):
        message = self.make_message(
            self.integration, mid="l-2", snippet="Без маркеров"
        )
        task = Task.objects.create(owner=self.user, name="Задача")
        message.linked_tasks.add(task)
        r = self._home()
        self.assertContains(r, "Без маркеров")

    def test_foreign_email_not_shown(self):
        foreign = self.make_message(
            self.make_integration(user=self.other, email="other@gmail.com"),
            mid="foreign-3",
        )
        r = self._home()
        self.assertNotContains(r, "Письма, требующие внимания")
        self.assertEqual(r.context["attention_emails"], [])

    def test_no_integration_page_works(self):
        EmailIntegration.objects.all().delete()
        r = self._home()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["attention_emails"], [])


class EmailDetailTest(IntegrationTestCase):
    """Просмотр письма: содержимое по требованию, кнопки Gmail/задачи."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.integration = self.make_integration()
        self.message = self.make_message(self.integration, mid="d-1")

    @mock.patch("integrations.services.gmail.get_message_full")
    def test_detail_shows_content_and_buttons(self, get_full):
        get_full.return_value = {
            "snippet": "Кратко",
            "body_text": "Полный текст письма",
            "html_text": "",
        }
        r = self.client.get(
            reverse("integrations:email_detail", kwargs={"pk": self.message.pk})
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Полный текст письма")
        self.assertContains(r, "Открыть в Gmail")
        self.assertContains(r, "Создать задачу")
        self.assertContains(r, "mail.google.com")
        get_full.assert_called_once()

    def test_inactive_integration_message_hidden(self):
        self.integration.is_active = False
        self.integration.save()
        r = self.client.get(
            reverse("integrations:email_detail", kwargs={"pk": self.message.pk})
        )
        self.assertEqual(r.status_code, 404)