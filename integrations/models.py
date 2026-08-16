"""Интеграции: подключение внешних сервисов (Gmail) и рабочие письма.

Принципы:
* Токены OAuth шифруются на уровне приложения (Fernet) и никогда
  не выводятся в HTML, логи, JSON или сообщения об ошибках.
* Локально хранятся только метаданные писем; полное содержимое
  запрашивается у Gmail API лишь при открытии письма.
* Один пользователь + один провайдер + один email = одна интеграция.
"""
from django.conf import settings
from django.db import models

from .services.crypto import decrypt_token, encrypt_token


class EmailIntegration(models.Model):
    """Подключённый почтовый аккаунт (Gmail через Google OAuth)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_integrations",
        verbose_name="Пользователь",
    )
    provider = models.CharField("Провайдер", max_length=20, default="gmail")
    email = models.EmailField("Почта аккаунта")

    # Токены хранятся зашифрованными (Fernet). Открытый текст доступен
    # только через decrypt_access_token()/decrypt_refresh_token().
    encrypted_access_token = models.TextField("Зашифрованный access token")
    encrypted_refresh_token = models.TextField("Зашифрованный refresh token")
    token_expires_at = models.DateTimeField(
        "Срок access token", null=True, blank=True
    )

    is_active = models.BooleanField("Подключено", default=True)
    last_sync_at = models.DateTimeField(
        "Последняя синхронизация", null=True, blank=True
    )
    created_at = models.DateTimeField("Дата подключения", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "provider", "email"],
                name="uniq_integration_per_user_provider_email",
            ),
        ]
        verbose_name = "Интеграция почты"
        verbose_name_plural = "Интеграции почты"

    def __str__(self):
        return f"{self.email} ({self.provider})"

    def decrypt_access_token(self):
        return decrypt_token(self.encrypted_access_token)

    def decrypt_refresh_token(self):
        return decrypt_token(self.encrypted_refresh_token)

    def set_tokens(self, access_token, refresh_token, token_expires_at):
        """Сохранить токены в зашифрованном виде."""
        self.encrypted_access_token = encrypt_token(access_token)
        self.encrypted_refresh_token = encrypt_token(refresh_token)
        self.token_expires_at = token_expires_at

    def clear_tokens(self):
        """Полностью стереть локальные токены."""
        self.encrypted_access_token = ""
        self.encrypted_refresh_token = ""
        self.token_expires_at = None


class EmailMessage(models.Model):
    """Метаданные рабочего письма. Полное содержимое не хранится."""

    integration = models.ForeignKey(
        EmailIntegration,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="Интеграция",
    )
    provider_message_id = models.CharField(
        "ID письма в Gmail", max_length=255, unique=True
    )
    thread_id = models.CharField("ID треды", max_length=255, blank=True)
    sender_name = models.CharField("Имя отправителя", max_length=255, blank=True)
    sender_email = models.EmailField("Почта отправителя", blank=True)
    subject = models.CharField("Тема", max_length=500, blank=True)
    snippet = models.TextField("Фрагмент", blank=True)
    received_at = models.DateTimeField("Получено")
    gmail_url = models.URLField("Ссылка на Gmail", blank=True)
    is_read = models.BooleanField("Прочитано", default=False)

    # Связи с рабочим контекстом. SET_NULL: отключение интеграции или
    # удаление письма не удаляет задачу, созданную из него.
    # Письмо, из которого задача СОЗДАНА, — EmailMessage.tasks (через
    # Task.source_email). Письмо, СВЯЗАННОЕ с существующей задачей, —
    # EmailMessage.linked_tasks (M2M).
    linked_tasks = models.ManyToManyField(
        "tasks.Task",
        blank=True,
        related_name="linked_emails",
        verbose_name="Связанные задачи",
    )
    projects = models.ManyToManyField(
        "tasks.Project",
        blank=True,
        related_name="emails",
        verbose_name="Проекты",
    )
    created_at = models.DateTimeField("Сохранено", auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "Письмо"
        verbose_name_plural = "Письма"

    def __str__(self):
        return self.subject or self.provider_message_id