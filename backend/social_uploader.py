"""
social_uploader.py — Загрузка видео в соцсети (YouTube, Instagram, TikTok).

YouTube: полная интеграция через YouTube Data API v3.
Instagram / TikTok: заглушки с инструкциями по подключению.
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class YouTubeUploader:
    """
    Upload videos to YouTube via YouTube Data API v3.
    Requires google-api-python-client and google-auth-oauthlib.
    """

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
    TOKEN_FILE = Path(__file__).parent / "youtube_token.json"
    CLIENT_SECRET_FILE = Path(__file__).parent / "client_secret.json"

    def __init__(self):
        self._credentials = None

    @property
    def is_configured(self) -> bool:
        """Check if client_secret.json exists (OAuth app configured)."""
        return self.CLIENT_SECRET_FILE.exists()

    @property
    def is_authenticated(self) -> bool:
        """Check if we have a valid saved token."""
        if not self.TOKEN_FILE.exists():
            return False
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(str(self.TOKEN_FILE), self.SCOPES)
            return creds.valid or (creds.expired and creds.refresh_token)
        except Exception:
            return False

    def get_auth_url(self) -> Optional[str]:
        """Generate an OAuth2 authorization URL."""
        if not self.is_configured:
            return None
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.CLIENT_SECRET_FILE),
                scopes=self.SCOPES,
                redirect_uri="http://localhost:8000/api/social/youtube/callback",
            )
            auth_url, _ = flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
            )
            return auth_url
        except Exception as e:
            logger.error(f"YouTube OAuth URL error: {e}")
            return None

    def handle_callback(self, code: str) -> bool:
        """Exchange authorization code for credentials and save token."""
        if not self.is_configured:
            return False
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.CLIENT_SECRET_FILE),
                scopes=self.SCOPES,
                redirect_uri="http://localhost:8000/api/social/youtube/callback",
            )
            flow.fetch_token(code=code)
            creds = flow.credentials
            with open(self.TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
            self._credentials = creds
            return True
        except Exception as e:
            logger.error(f"YouTube OAuth callback error: {e}")
            return False

    def _get_credentials(self):
        """Load and refresh credentials."""
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        if self._credentials and self._credentials.valid:
            return self._credentials

        if self.TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(self.TOKEN_FILE), self.SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(self.TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
            self._credentials = creds
            return creds
        return None

    def upload(
        self,
        video_path: str,
        title: str = "Untitled",
        description: str = "",
        tags: list[str] = None,
        privacy: str = "private",  # "private" | "unlisted" | "public"
        category_id: str = "22",   # "People & Blogs"
    ) -> dict:
        """
        Upload a video to YouTube. Returns {"video_id": ..., "url": ...}.
        Raises RuntimeError on failure.
        """
        creds = self._get_credentials()
        if not creds or not creds.valid:
            raise RuntimeError("YouTube не авторизован. Подключите аккаунт в настройках.")

        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload

            youtube = build("youtube", "v3", credentials=creds)

            body = {
                "snippet": {
                    "title": title[:100],
                    "description": description[:5000],
                    "tags": (tags or [])[:30],
                    "categoryId": category_id,
                },
                "status": {
                    "privacyStatus": privacy,
                    "selfDeclaredMadeForKids": False,
                },
            }

            media = MediaFileUpload(
                video_path,
                mimetype="video/mp4",
                resumable=True,
                chunksize=10 * 1024 * 1024,
            )

            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                _, response = request.next_chunk()

            video_id = response.get("id", "")
            return {
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "status": "uploaded",
            }

        except Exception as e:
            raise RuntimeError(f"Ошибка загрузки на YouTube: {str(e)}")

    def disconnect(self):
        """Remove saved token."""
        self.TOKEN_FILE.unlink(missing_ok=True)
        self._credentials = None


class InstagramUploader:
    """
    Instagram upload — заглушка.
    Для полноценной интеграции требуется:
    1. Зарегистрировать приложение на developers.facebook.com
    2. Получить доступ к Instagram Graph API (Content Publishing)
    3. Пройти App Review от Meta
    """

    @property
    def is_configured(self) -> bool:
        return False

    @property
    def is_authenticated(self) -> bool:
        return False

    def upload(self, video_path: str, **kwargs) -> dict:
        raise RuntimeError(
            "Загрузка в Instagram пока недоступна.\n\n"
            "Для подключения необходимо:\n"
            "1. Создать приложение на developers.facebook.com\n"
            "2. Включить Instagram Graph API → Content Publishing\n"
            "3. Пройти App Review от Meta\n"
            "4. Добавить access_token в настройки"
        )

    @staticmethod
    def get_setup_instructions() -> str:
        return (
            "📸 Instagram: Требуется Meta Business Account и App Review.\n"
            "1. Перейдите на developers.facebook.com\n"
            "2. Создайте приложение типа 'Business'\n"
            "3. Включите Instagram Graph API → Content Publishing\n"
            "4. Пройдите App Review\n"
            "5. Сгенерируйте Long-Lived Access Token"
        )


class TikTokUploader:
    """
    TikTok upload — заглушка.
    Для полноценной интеграции требуется:
    1. Зарегистрировать приложение на developers.tiktok.com
    2. Получить доступ к Content Posting API
    3. Пройти App Review от TikTok
    """

    @property
    def is_configured(self) -> bool:
        return False

    @property
    def is_authenticated(self) -> bool:
        return False

    def upload(self, video_path: str, **kwargs) -> dict:
        raise RuntimeError(
            "Загрузка в TikTok пока недоступна.\n\n"
            "Для подключения необходимо:\n"
            "1. Создать приложение на developers.tiktok.com\n"
            "2. Запросить доступ к Content Posting API\n"
            "3. Пройти App Review от TikTok\n"
            "4. Добавить client_key и client_secret в настройки"
        )

    @staticmethod
    def get_setup_instructions() -> str:
        return (
            "🎵 TikTok: Требуется TikTok for Developers аккаунт.\n"
            "1. Перейдите на developers.tiktok.com\n"
            "2. Создайте приложение\n"
            "3. Запросите Content Posting API scope\n"
            "4. Пройдите App Review от TikTok\n"
            "5. Получите Client Key и Client Secret"
        )


# Singleton instances
youtube_uploader = YouTubeUploader()
instagram_uploader = InstagramUploader()
tiktok_uploader = TikTokUploader()
