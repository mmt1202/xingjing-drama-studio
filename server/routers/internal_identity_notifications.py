"""Service-to-service password reset notification delivery."""

from __future__ import annotations

import asyncio
import html
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from hmac import compare_digest
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/internal/identity", tags=["identity-internal"])


class NotificationVariables(BaseModel):
    resetToken: str = Field(min_length=32, max_length=512)


class IdentityNotificationRequest(BaseModel):
    templateKey: str = Field(min_length=1, max_length=128)
    recipient: str = Field(min_length=3, max_length=320)
    variables: NotificationVariables


class IdentityNotificationResponse(BaseModel):
    messageId: str


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    username: str | None
    password: str | None
    sender: str
    public_app_url: str

    @classmethod
    def from_environment(cls) -> SmtpSettings:
        host = os.environ.get("XINGJING_SMTP_HOST", "").strip()
        sender = os.environ.get("XINGJING_SMTP_FROM", "").strip()
        public_app_url = os.environ.get("XINGJING_PUBLIC_APP_URL", "").strip().rstrip("/")
        security_mode = os.environ.get("XINGJING_SMTP_SECURITY", "starttls").strip().casefold()
        username = os.environ.get("XINGJING_SMTP_USERNAME", "").strip() or None
        password = os.environ.get("XINGJING_SMTP_PASSWORD", "").strip() or None
        try:
            port = int(os.environ.get("XINGJING_SMTP_PORT", "587"))
        except ValueError as error:
            raise NotificationConfigurationError from error
        if (
            not host
            or not sender
            or not public_app_url
            or port < 1
            or port > 65535
            or security_mode not in {"starttls", "ssl", "plain"}
            or (username is None) != (password is None)
        ):
            raise NotificationConfigurationError
        sender_address = parseaddr(sender)[1]
        if not sender_address or "@" not in sender_address or any(character in sender for character in "\r\n"):
            raise NotificationConfigurationError
        if not public_app_url.startswith(("https://", "http://")):
            raise NotificationConfigurationError
        return cls(host, port, security_mode, username, password, sender, public_app_url)


class NotificationConfigurationError(RuntimeError):
    pass


class NotificationDeliveryError(RuntimeError):
    pass


@router.post("/notifications", response_model=IdentityNotificationResponse)
async def deliver_identity_notification(
    payload: IdentityNotificationRequest,
    authorization: str = Header(default=""),
) -> IdentityNotificationResponse:
    _authorize_service(authorization)
    if payload.templateKey != "identity.password-reset":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NOTIFICATION_TEMPLATE_NOT_ALLOWED"},
        )
    if not _is_plain_email_address(payload.recipient):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NOTIFICATION_RECIPIENT_INVALID"},
        )
    try:
        settings = SmtpSettings.from_environment()
        message_id = await asyncio.to_thread(
            _send_password_reset,
            settings,
            payload.recipient,
            payload.variables.resetToken,
        )
    except NotificationConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "NOTIFICATION_DELIVERY_NOT_CONFIGURED"},
        ) from error
    except NotificationDeliveryError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "NOTIFICATION_DELIVERY_FAILED"},
        ) from error
    return IdentityNotificationResponse(messageId=message_id)


def _authorize_service(authorization: str) -> None:
    expected = os.environ.get("XINGJING_IDENTITY_NOTIFICATION_TOKEN", "").strip()
    provided = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    if len(expected) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "SERVICE_AUTHENTICATION_NOT_CONFIGURED"},
        )
    if not provided or not compare_digest(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "SERVICE_AUTHENTICATION_REQUIRED"},
        )


def _send_password_reset(settings: SmtpSettings, recipient: str, reset_token: str) -> str:
    query = urlencode({"token": reset_token})
    reset_url = f"{settings.public_app_url}/password-reset/confirm?{query}"
    message_id = make_msgid(domain=_message_domain(settings.sender))
    message = EmailMessage()
    message["Subject"] = "重置你的星镜剧创密码"
    message["From"] = settings.sender
    message["To"] = recipient
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = message_id
    message.set_content(
        "你正在重置星镜剧创账号密码。\n\n"
        f"请在 30 分钟内打开以下链接：\n{reset_url}\n\n"
        "如果不是你本人操作，请忽略此邮件。"
    )
    safe_url = html.escape(reset_url, quote=True)
    message.add_alternative(
        "<p>你正在重置星镜剧创账号密码。</p>"
        f'<p><a href="{safe_url}">在 30 分钟内重置密码</a></p>'
        "<p>如果不是你本人操作，请忽略此邮件。</p>",
        subtype="html",
    )
    context = ssl.create_default_context()
    try:
        if settings.security == "ssl":
            with smtplib.SMTP_SSL(settings.host, settings.port, timeout=15, context=context) as client:
                _authenticate_and_send(client, settings, message)
        else:
            with smtplib.SMTP(settings.host, settings.port, timeout=15) as client:
                client.ehlo()
                if settings.security == "starttls":
                    client.starttls(context=context)
                    client.ehlo()
                _authenticate_and_send(client, settings, message)
    except (OSError, smtplib.SMTPException) as error:
        raise NotificationDeliveryError from error
    return message_id


def _authenticate_and_send(client: smtplib.SMTP, settings: SmtpSettings, message: EmailMessage) -> None:
    if settings.username is not None and settings.password is not None:
        client.login(settings.username, settings.password)
    refused = client.send_message(message)
    if refused:
        raise NotificationDeliveryError


def _message_domain(sender: str) -> str | None:
    address = sender.rsplit("<", 1)[-1].rstrip(">")
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    return domain or None


def _is_plain_email_address(value: str) -> bool:
    if any(character in value for character in "\r\n"):
        return False
    display_name, address = parseaddr(value)
    return not display_name and address == value and "@" in address
