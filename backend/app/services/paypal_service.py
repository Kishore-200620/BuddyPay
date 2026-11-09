import logging

import httpx

from app.core.config import settings
import uuid
from app.core.file_storage import MAX_FILE_SIZE, validate_pdf
from decimal import Decimal, InvalidOperation
logger = logging.getLogger(__name__)

PAYPAL_SUPPORTED_CURRENCIES = {
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "AUD",
    "JPY",
}
async def get_paypal_access_token() -> dict:
    """
    Authenticate with PayPal Sandbox and retrieve an OAuth access token.
    """

    url = f"{settings.PAYPAL_BASE_URL}/v1/oauth2/token"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                auth=(
                    settings.PAYPAL_CLIENT_ID,
                    settings.PAYPAL_CLIENT_SECRET,
                ),
                data={
                    "grant_type": "client_credentials",
                },
                headers={
                    "Accept": "application/json",
                    "Accept-Language": "en_US",
                },
            )

            response.raise_for_status()

            token_data = response.json()

            access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in")

            if not access_token or not expires_in:
                raise ValueError(
                    "PayPal returned an incomplete OAuth response"
                )

            return {
                "access_token": access_token,
                "token_type": token_data.get("token_type"),
                "expires_in": expires_in,
            }

    except httpx.HTTPStatusError as exc:
        logger.error(
            "PayPal OAuth failed with HTTP status %s",
            exc.response.status_code,
        )
        raise RuntimeError(
            "PayPal authentication failed. Check Sandbox credentials."
        ) from exc

    except httpx.RequestError as exc:
        logger.error("PayPal connection failed: %s", type(exc).__name__)
        raise RuntimeError(
            "Could not connect to PayPal Sandbox."
        ) from exc

def _paypal_experience_context() -> dict:
    return {
        "brand_name": "BuddyPay",
        "landing_page": "LOGIN",
        "user_action": "PAY_NOW",
        "shipping_preference": "NO_SHIPPING",
        "return_url": "http://localhost:3000/paypal/return",
        "cancel_url": "http://localhost:3000/paypal/cancel",
    }
async def create_paypal_order(
    amount: str,
    currency_code: str,
    reference_id: str,
) -> dict:
    """
    Create a PayPal Sandbox order without capturing payment.
    """

    try:
        payment_amount = Decimal(amount)

        if not payment_amount.is_finite() or payment_amount <= 0:
            raise ValueError("Payment amount must be positive")

        if payment_amount.as_tuple().exponent < -2:
            raise ValueError("Payment amount cannot have more than 2 decimal places")

        payment_amount = format(payment_amount, ".2f")

    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid payment amount") from exc

    if (
        len(currency_code) != 3
        or not currency_code.isalpha()
        or not currency_code.isascii()
    ):
        raise ValueError("Invalid currency code")

    if not reference_id or len(reference_id) > 256:
        raise ValueError("Invalid reference ID")

    token_data = await get_paypal_access_token()

    url = f"{settings.PAYPAL_BASE_URL}/v2/checkout/orders"

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": reference_id,
                "amount": {
                    "currency_code": currency_code.upper(),
                    "value": payment_amount,
                },
            }
        ],
        "payment_source": {
            "paypal": {
                "experience_context": _paypal_experience_context(),
            }
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token_data['access_token']}",
        "PayPal-Request-Id": str(uuid.uuid4()),
        "Prefer": "return=representation",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )

            response.raise_for_status()

            order_data = response.json()

            if not order_data.get("id"):
                raise ValueError("PayPal returned an incomplete order response")

            return order_data

    except httpx.HTTPStatusError as exc:
        logger.error(
            "PayPal order creation failed with HTTP status %s",
            exc.response.status_code,
        )
        raise RuntimeError("PayPal order creation failed") from exc

    except httpx.RequestError as exc:
        logger.error(
            "PayPal order connection failed: %s",
            type(exc).__name__,
        )
        raise RuntimeError("Could not connect to PayPal Orders API") from exc
async def get_paypal_us_access_token() -> dict:
    url = f"{settings.PAYPAL_US_BASE_URL}/v1/oauth2/token"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                auth=(
                    settings.PAYPAL_US_CLIENT_ID,
                    settings.PAYPAL_US_CLIENT_SECRET,
                ),
                data={"grant_type": "client_credentials"},
                headers={
                    "Accept": "application/json",
                    "Accept-Language": "en_US",
                },
            )

            response.raise_for_status()
            token_data = response.json()

            access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in")

            if not access_token or not expires_in:
                raise ValueError(
                    "PayPal returned an incomplete OAuth response"
                )

            return {
                "access_token": access_token,
                "token_type": token_data.get("token_type"),
                "expires_in": expires_in,
            }

    except httpx.HTTPStatusError as exc:
        logger.error(
            "US PayPal OAuth failed with HTTP status %s",
            exc.response.status_code,
        )
        raise RuntimeError(
            "US PayPal authentication failed. Check Sandbox credentials."
        ) from exc

    except httpx.RequestError as exc:
        logger.error(
            "US PayPal connection failed: %s",
            type(exc).__name__,
        )
        raise RuntimeError(
            "Could not connect to US PayPal Sandbox."
        ) from exc
async def create_paypal_us_order(
    amount: str,
    currency_code: str,
    reference_id: str,
    request_id: str | None = None,
) -> dict:
    from decimal import Decimal, InvalidOperation

    try:
        payment_amount = Decimal(amount)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid payment amount")

    if not payment_amount.is_finite() or payment_amount <= 0:
        raise ValueError("Payment amount must be positive")

    if payment_amount.as_tuple().exponent < -2:
        raise ValueError("Payment amount cannot have more than 2 decimal places")

    if (
        len(currency_code) != 3
        or not currency_code.isascii()
        or not currency_code.isalpha()
    ):
        raise ValueError("Currency code must contain 3 letters")

    currency_code = currency_code.upper()

    if currency_code not in PAYPAL_SUPPORTED_CURRENCIES:
        raise ValueError(
            f"Currency {currency_code} is not supported for PayPal checkout. "
            f"Supported currencies: "
            f"{', '.join(sorted(PAYPAL_SUPPORTED_CURRENCIES))}"
        )
    if not reference_id or len(reference_id) > 256:
        raise ValueError("Invalid reference ID")

    token_data = await get_paypal_us_access_token()

    url = f"{settings.PAYPAL_US_BASE_URL}/v2/checkout/orders"

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": reference_id,
                "amount": {
                    "currency_code": currency_code.upper(),
                    "value": format(payment_amount, ".2f"),
                },
            }
        ],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "brand_name": "BuddyPay",
                    "landing_page": "LOGIN",
                    "user_action": "PAY_NOW",
                    "shipping_preference": "NO_SHIPPING",
                    "return_url": "http://localhost:3000/paypal/return",
                    "cancel_url": "http://localhost:3000/paypal/cancel",
                }
            }
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token_data['access_token']}",
        "PayPal-Request-Id": request_id or str(uuid.uuid4()),
        "Prefer": "return=representation",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    except httpx.HTTPStatusError as exc:
        logger.error(
            "PayPal order creation failed. HTTP status: %s | Response: %s",
            exc.response.status_code,
            exc.response.text,
        )
        raise RuntimeError(
            "PayPal order creation failed."
        ) from exc

    except httpx.RequestError as exc:
        logger.error(
            "US PayPal connection failed: %s",
            type(exc).__name__,
        )
        raise RuntimeError(
            "Could not connect to US PayPal Sandbox."
        ) from exc
async def get_paypal_us_order(order_id: str) -> dict:
    """Retrieve a PayPal Sandbox order using the US merchant credentials."""

    if not order_id or len(order_id) > 100:
        raise ValueError("Invalid PayPal order ID")

    token_data = await get_paypal_us_access_token()
    url = f"{settings.PAYPAL_US_BASE_URL}/v2/checkout/orders/{order_id}"

    headers = {
        "Authorization": f"Bearer {token_data['access_token']}",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()

    except httpx.HTTPStatusError as exc:
        logger.error(
            "US PayPal order retrieval failed: HTTP %s; PayPal-Debug-Id=%s",
            exc.response.status_code,
            exc.response.headers.get("PayPal-Debug-Id", "unavailable"),
        )
        raise RuntimeError("US PayPal order retrieval failed") from exc

    except httpx.RequestError as exc:
        logger.error(
            "US PayPal order retrieval connection failed: %s",
            type(exc).__name__,
        )
        raise RuntimeError("Could not connect to US PayPal Orders API") from exc
async def capture_paypal_us_order(order_id: str) -> dict:
    """Capture an approved US PayPal Sandbox order."""

    if not order_id or len(order_id) > 100:
        raise ValueError("Invalid PayPal order ID")

    token_data = await get_paypal_us_access_token()
    url = (
        f"{settings.PAYPAL_US_BASE_URL}"
        f"/v2/checkout/orders/{order_id}/capture"
    )

    headers = {
        "Authorization": f"Bearer {token_data['access_token']}",
        "Content-Type": "application/json",
        "PayPal-Request-Id": f"capture-{order_id}",
        "Prefer": "return=representation",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json={})
            response.raise_for_status()
            return response.json()

    except httpx.HTTPStatusError as exc:
        logger.error(
            "US PayPal capture failed: HTTP %s; PayPal-Debug-Id=%s",
            exc.response.status_code,
            exc.response.headers.get("PayPal-Debug-Id", "unavailable"),
        )
        raise RuntimeError("US PayPal capture failed") from exc

    except httpx.RequestError as exc:
        logger.error("US PayPal capture connection failed: %s", type(exc).__name__)
        raise RuntimeError("Could not connect to US PayPal Orders API") from exc
async def verify_paypal_us_webhook_signature(
    *,
    transmission_id: str | None,
    transmission_time: str | None,
    cert_url: str | None,
    auth_algo: str | None,
    transmission_sig: str | None,
    webhook_event: dict,
) -> bool:
    """Verify a PayPal webhook using PayPal's verification API."""

    webhook_id = settings.PAYPAL_US_WEBHOOK_ID

    if not webhook_id:
        raise RuntimeError("PAYPAL_US_WEBHOOK_ID is not configured.")

    required_headers = {
        "transmission_id": transmission_id,
        "transmission_time": transmission_time,
        "cert_url": cert_url,
        "auth_algo": auth_algo,
        "transmission_sig": transmission_sig,
    }

    missing_headers = [
        name for name, value in required_headers.items() if not value
    ]

    if missing_headers:
        raise ValueError(
            "Missing PayPal webhook headers: "
            + ", ".join(missing_headers)
        )

    if not isinstance(webhook_event, dict) or not webhook_event:
        raise ValueError("Invalid PayPal webhook event payload.")

    token_data = await get_paypal_us_access_token()

    url = (
        f"{settings.PAYPAL_US_BASE_URL}"
        "/v1/notifications/verify-webhook-signature"
    )

    payload = {
        "transmission_id": transmission_id,
        "transmission_time": transmission_time,
        "cert_url": cert_url,
        "auth_algo": auth_algo,
        "transmission_sig": transmission_sig,
        "webhook_id": webhook_id,
        "webhook_event": webhook_event,
    }

    headers = {
        "Authorization": f"Bearer {token_data['access_token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )

            response.raise_for_status()
            verification_data = response.json()

        if not isinstance(verification_data, dict):
            raise RuntimeError("Invalid PayPal verification response.")

        verification_status = verification_data.get("verification_status")

        if verification_status == "SUCCESS":
            return True

        if verification_status == "FAILURE":
            logger.warning("PayPal webhook signature verification failed.")
            return False

        logger.error("Unexpected PayPal webhook verification response.")
        raise RuntimeError("Unexpected PayPal verification status.")

    except httpx.HTTPStatusError as exc:
        logger.error(
            "PayPal webhook verification API failed: HTTP %s; "
            "PayPal-Debug-Id=%s",
            exc.response.status_code,
            exc.response.headers.get("PayPal-Debug-Id", "unavailable"),
        )
        raise RuntimeError(
            "PayPal webhook verification API request failed."
        ) from exc

    except httpx.RequestError as exc:
        logger.error(
            "PayPal webhook verification connection failed: %s",
            type(exc).__name__,
        )
        raise RuntimeError(
            "Could not connect to PayPal verification API."
        ) from exc

    except ValueError as exc:
        logger.error("Invalid PayPal verification API response.")
        raise RuntimeError(
            "PayPal returned an invalid verification response."
        ) from exc