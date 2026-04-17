import json
import logging
import re
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError

from django.conf import settings

logger = logging.getLogger(__name__)

SMS_LEOPARD_SEND_URL = "https://api.smsleopard.com/v1/sms/send"


def normalize_phone_number(phone_number: str | None) -> str | None:
    if not phone_number:
        return None

    digits = re.sub(r"\D", "", str(phone_number))
    if not digits:
        return None

    if digits.startswith("254") and len(digits) == 12 and digits[3] in {"7", "1"}:
        return digits

    if digits.startswith("0") and len(digits) == 10 and digits[1] in {"7", "1"}:
        return f"254{digits[1:]}"

    if len(digits) == 9 and digits[0] in {"7", "1"}:
        return f"254{digits}"

    return None


def normalize_phone_numbers(destinations: str | Iterable[str] | None) -> list[str]:
    if not destinations:
        return []

    if isinstance(destinations, str):
        raw_numbers = [part.strip() for part in destinations.split(",") if part.strip()]
    else:
        raw_numbers = [str(item).strip() for item in destinations if str(item).strip()]

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_numbers:
        number = normalize_phone_number(raw)
        if not number or number in seen:
            continue
        seen.add(number)
        normalized.append(number)

    return normalized


def _is_sms_configured() -> bool:
    api_key = str(getattr(settings, "SMS_LEOPARD_API_KEY", "") or "").strip()
    api_secret = str(getattr(settings, "SMS_LEOPARD_API_SECRET", "") or "").strip()
    return bool(api_key and api_secret)


def _looks_successful(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) == 1
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "ok", "success", "sent"}:
            return True
        if normalized in {"0", "false", "no", "failed", "error", "unsuccessful"}:
            return False
    return False


def _sms_provider_success(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False

    success_value = payload.get("success")
    if success_value is not None:
        return _looks_successful(success_value)

    status_value = payload.get("status")
    if status_value is not None:
        return _looks_successful(status_value)

    return False


def _sms_provider_reason(payload: dict) -> str:
    if not isinstance(payload, dict):
        return "provider_unsuccessful_response"
    for key in ("message", "error", "detail", "status"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return "provider_unsuccessful_response"


def send_sms_via_leopard(
    *,
    message: str,
    destinations: str | Iterable[str] | None,
    log_extra: dict | None = None,
) -> dict:
    recipients = normalize_phone_numbers(destinations)
    if not recipients:
        logger.warning(
            "SMS skipped because no valid recipients were found",
            extra={**(log_extra or {}), "destinations": destinations},
        )
        return {"success": False, "reason": "no_recipients"}

    if not _is_sms_configured():
        logger.warning(
            "SMS Leopard credentials missing. Skipping SMS send.",
            extra=log_extra or {},
        )
        return {"success": False, "reason": "not_configured", "recipients": recipients}

    params = {
        "username": str(getattr(settings, "SMS_LEOPARD_API_KEY", "") or "").strip(),
        "password": str(getattr(settings, "SMS_LEOPARD_API_SECRET", "") or "").strip(),
        "message": message,
        "destination": ",".join(recipients),
        "source": str(getattr(settings, "SMS_LEOPARD_SOURCE", "SMS_Leopard") or "SMS_Leopard").strip(),
    }

    url = f"{SMS_LEOPARD_SEND_URL}?{urlencode(params)}"

    try:
        with urlopen(url, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body) if body else {}
    except HTTPError as exc:
        error_body = ""
        payload = {}
        try:
            if exc.fp:
                error_body = exc.read().decode("utf-8", errors="replace")
                payload = json.loads(error_body) if error_body else {}
        except Exception:
            payload = {}

        reason = _sms_provider_reason(payload)
        if reason == "provider_unsuccessful_response":
            reason = str(exc.reason or "").strip() or f"http_{exc.code}"

        logger.warning(
            "SMS Leopard request rejected (HTTP %s): %s",
            exc.code,
            reason,
            extra={
                **(log_extra or {}),
                "http_status": exc.code,
                "reason": reason,
                "sms_response": payload,
                "sms_response_raw": error_body[:1000],
            },
        )
        return {
            "success": False,
            "reason": reason,
            "http_status": int(exc.code),
            "response": payload,
            "response_raw": error_body,
            "recipients": recipients,
        }
    except Exception:
        logger.exception("Failed to send SMS via SMS Leopard", extra=log_extra or {})
        return {"success": False, "reason": "request_error", "recipients": recipients}

    success = _sms_provider_success(payload)
    if not success:
        reason = _sms_provider_reason(payload)
        logger.warning(
            "SMS Leopard request returned unsuccessful response: %s",
            reason,
            extra={**(log_extra or {}), "sms_response": payload, "reason": reason},
        )
        return {"success": False, "reason": reason, "response": payload, "recipients": recipients}

    logger.info(
        "SMS Leopard request accepted",
        extra={**(log_extra or {}), "recipient_count": len(recipients)},
    )

    return {"success": success, "response": payload, "recipients": recipients}
