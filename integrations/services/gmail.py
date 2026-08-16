"""Gmail API: read-only клиент на стандартной библиотеке.

Правила:
* Запрашиваются только метаданные писем (список, snippet, заголовки);
* полное содержимое — только когда пользователь открывает письмо
  (get_message_full);
* содержимое писем никогда не логируется.
"""
import base64
import email
import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.utils import timezone

from . import oauth

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailError(Exception):
    """Ошибка обращения к Gmail API с понятным сообщением."""


def _get_json(url, access_token):
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise GmailError("Токен доступа недействителен или отозван.") from exc
        if exc.code == 403:
            raise GmailError("Gmail вернул отказ в доступе (403).") from exc
        if exc.code == 429:
            raise GmailError("Превышен лимит запросов к Gmail API.") from exc
        raise GmailError(f"Gmail API вернул ошибку (HTTP {exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise GmailError("Не удалось связаться с Gmail API.") from exc


def get_profile(access_token):
    """Email адрес подключённого аккаунта."""
    return _get_json(f"{GMAIL_API}/profile", access_token)


def _message_url(message_id, fmt="metadata"):
    return (
        f"{GMAIL_API}/messages/{message_id}?format={fmt}"
        "&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date"
    )


def list_message_ids(access_token, query, max_results):
    """ID писем по запросу Gmail (например, newer_than:30d)."""
    params = "&".join(
        [
            f"q={urllib.parse.quote(query)}",
            f"maxResults={max_results}",
        ]
    )
    payload = _get_json(f"{GMAIL_API}/messages?{params}", access_token)
    return [item["id"] for item in payload.get("messages", [])]


def get_message_metadata(access_token, message_id):
    """Метаданные одного письма (без содержимого)."""
    payload = _get_json(_message_url(message_id), access_token)
    headers = {h["name"]: h["value"] for h in payload.get("payload", {}).get("headers", [])}
    return {
        "thread_id": payload.get("threadId", ""),
        "sender_name": _sender_name(headers.get("From", "")),
        "sender_email": _sender_email(headers.get("From", "")),
        "subject": headers.get("Subject", ""),
        "snippet": payload.get("snippet", ""),
        "received_at": _parse_date(headers.get("Date", "")),
        "is_read": not any(
            label == "UNREAD" for label in payload.get("labelIds", [])
        ),
        "gmail_url": f"https://mail.google.com/mail/u/0/#inbox/{payload.get('id')}",
    }


def get_message_full(access_token, message_id):
    """Полное содержимое письма: {snippet, body_text, html_text}.

    Вызывается только при открытии письма пользователем.
    """
    payload = _get_json(_message_url(message_id, fmt="full"), access_token)
    body_text, html_text = _extract_bodies(payload.get("payload", {}))
    return {
        "snippet": payload.get("snippet", ""),
        "body_text": body_text,
        "html_text": html_text,
    }


def _extract_bodies(part):
    text = []
    html = []
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        text.append(_decode_body(part["body"]["data"]))
    if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
        html.append(_decode_body(part["body"]["data"]))
    for child in part.get("parts", []):
        child_text, child_html = _extract_bodies(child)
        text.extend(child_text)
        html.extend(child_html)
    return "\n".join(text), "\n".join(html)


def _decode_body(data):
    try:
        return base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", "replace")
    except Exception:
        return ""


def _sender_name(from_header):
    parsed = email.utils.parseaddr(from_header)
    return (parsed[0] or "").strip()


def _sender_email(from_header):
    return email.utils.parseaddr(from_header)[1].lower()


def _parse_date(date_header):
    """Дата письма из заголовка (RFC 2822); падает на сейчас."""
    try:
        return email.utils.parsedate_to_datetime(date_header)
    except Exception:
        return timezone.now()


def build_sync_query(integration, now=None):
    """Gmail-запрос для получения только новых писем.

    Первая синхронизация — последние GMAIL_INITIAL_SYNC_DAYS дней;
    последующие — период с последней синхронизации (с запасом).
    """
    now = now or timezone.now()
    if integration.last_sync_at:
        delta = now - integration.last_sync_at
        days = max(1, int(delta.total_seconds() // 86400) + 1)
    else:
        days = getattr(settings, "GMAIL_INITIAL_SYNC_DAYS", 30)
    return f"newer_than:{days}d"