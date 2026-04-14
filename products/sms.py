import json
import logging
import re
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings

logger = logging.getLogger(__name__)

SMS_LEOPARD_SEND_URL = "https://api.smsleopard.com/v1/sms/send"


def normalize_phone_number(phone_number: str | None) -> str | None:
    if not phone_number:
        return None

    digits = re.sub(r"\D", "", str(phone_number))
    if not digits:
        return None

    if digits.startswith("254") and len(digits) == 12:
        return digits

    if digits.startswith("0") and len(digits) == 10:
        return f"254{digits[1:]}"

    if len(digits) == 9 and digits[0] in {"7", "1"}:
        return f"254{digits}"

    return digits


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


def send_sms_via_leopard(
    *,
    message: str,
    destinations: str | Iterable[str] | None,
    log_extra: dict | None = None,
) -> dict:
    recipients = normalize_phone_numbers(destinations)
    if not recipients:
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
    except Exception:
        logger.exception("Failed to send SMS via SMS Leopard", extra=log_extra or {})
        return {"success": False, "reason": "request_error", "recipients": recipients}

    success = bool(payload.get("success"))
    if not success:
        logger.warning(
            "SMS Leopard request returned unsuccessful response",
            extra={**(log_extra or {}), "sms_response": payload},
        )

    return {"success": success, "response": payload, "recipients": recipients}
