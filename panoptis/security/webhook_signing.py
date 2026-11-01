import hashlib
import hmac


def generate_webhook_signature(payload: bytes, secret: str) -> str:
    """Generate an HMAC-SHA256 signature for a webhook payload.

    Args:
        payload: The exact bytes of the HTTP request body that will be sent.
        secret: The shared secret key used for signing.

    Returns:
        The signature string in the format 'sha256=<hex_digest>'.
    """
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
