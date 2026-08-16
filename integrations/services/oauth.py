"""Google OAuth 2.0 (web server flow) на стандартной библиотеке.

Никаких сторонних клиентских библиотек: обмен кода и обновление токена
выполняются прямыми запросами к https://oauth2.googleapis.com/token.
"""
import json
import urllib.parse
import urllib.request

from django.conf import settings
from django.utils import timezone

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Read-only: просмотр писем и профиля. Ничего больше.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]


class OAuthError(Exception):
    """Ошибка OAuth-потока с понятным сообщением для пользователя."""


def _post_form(url, data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {}
        error = payload.get("error_description") or payload.get("error")
        raise OAuthError(
            "Google вернул ошибку авторизации."
            + (f" ({error})" if error else "")
        ) from exc


def build_auth_url(state):
    """Ссылка на страницу разрешения Google."""
    params = {
        "client_id": settings.GMAIL_CLIENT_ID,
        "redirect_uri": settings.GMAIL_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code):
    """Обменять авторизационный код на токены.

    Возвращает dict: {access_token, refresh_token, expires_in}.
    Если Google не выдал refresh_token (например, токен уже был),
    генерируется OAuthError — пользователь повторит подключение.
    """
    payload = _post_form(
        TOKEN_URL,
        {
            "client_id": settings.GMAIL_CLIENT_ID,
            "client_secret": settings.GMAIL_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.GMAIL_REDIRECT_URI,
        },
    )
    if "refresh_token" not in payload:
        raise OAuthError(
            "Google не выдал refresh token. Подключите Gmail ещё раз."
        )
    return payload


def refresh_access_token(refresh_token):
    """Получить новый access token по refresh token."""
    payload = _post_form(
        TOKEN_URL,
        {
            "client_id": settings.GMAIL_CLIENT_ID,
            "client_secret": settings.GMAIL_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    return payload


def revoke_token(refresh_token):
    """Отозвать авторизацию на стороне Google (best effort)."""
    body = urllib.parse.urlencode({"token": refresh_token}).encode("utf-8")
    request = urllib.request.Request(
        REVOKE_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True
    except urllib.error.HTTPError:
        # Google 400 при повторном отзыве — считаем отозванным.
        return True
    except urllib.error.URLError:
        # Сеть недоступна — локальные токены всё равно удаляются.
        return False


def expires_at_from_payload(payload):
    """datetime, когда истекает access token из payload Google."""
    return timezone.now() + timezone.timedelta(
        seconds=int(payload.get("expires_in", 3600))
    )