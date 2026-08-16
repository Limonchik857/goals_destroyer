"""Синхронизация писем Gmail с локальной базой (только метаданные).

Incremental: каждая синхронизация запрашивает письма не старше периода
с последней синхронизации; `EmailMessage` обновляется через
update_or_create по provider_message_id — дубликаты невозможны.
"""
from django.utils import timezone

from ..models import EmailMessage
from . import gmail, oauth

SYNC_PAGE_SIZE = 100


class SyncError(Exception):
    """Ошибка синхронизации с понятным сообщением для пользователя."""


def _fresh_access_token(integration):
    """Обновить access token, если он истёк или скоро истечёт."""
    token = integration.decrypt_access_token()
    if not token:
        raise SyncError("Токены интеграции удалены. Подключите Gmail снова.")
    if integration.token_expires_at and (
        integration.token_expires_at - timezone.now()
    ).total_seconds() < 300:
        payload = oauth.refresh_access_token(integration.decrypt_refresh_token())
        integration.set_tokens(
            payload["access_token"],
            integration.decrypt_refresh_token(),
            oauth.expires_at_from_payload(payload),
        )
        integration.save(update_fields=[
            "encrypted_access_token", "token_expires_at", "updated_at",
        ])
        token = integration.decrypt_access_token()
    return token


def sync_messages(integration, max_results=SYNC_PAGE_SIZE):
    """Синхронизировать письма интеграции. Возвращает количество новых.

    Интеграция должна быть активной и принадлежать пользователю —
    проверяется вызывающим кодом.
    """
    if not integration.is_active:
        raise SyncError("Интеграция отключена.")
    token = _fresh_access_token(integration)
    query = gmail.build_sync_query(integration)
    ids = gmail.list_message_ids(token, query, max_results=max_results)

    created = 0
    for message_id in ids:
        meta = gmail.get_message_metadata(token, message_id)
        _, was_created = EmailMessage.objects.update_or_create(
            provider_message_id=message_id,
            defaults={
                "integration": integration,
                "thread_id": meta["thread_id"],
                "sender_name": meta["sender_name"],
                "sender_email": meta["sender_email"],
                "subject": meta["subject"][:500],
                "snippet": meta["snippet"],
                "received_at": meta["received_at"],
                "gmail_url": meta["gmail_url"],
                "is_read": meta["is_read"],
            },
        )
        created += int(was_created)

    integration.last_sync_at = timezone.now()
    integration.save(update_fields=["last_sync_at", "updated_at"])
    return created


def fetch_message_full(integration, message):
    """Полное содержимое письма для страницы просмотра."""
    token = _fresh_access_token(integration)
    return gmail.get_message_full(token, message.provider_message_id)