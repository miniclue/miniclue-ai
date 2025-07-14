import logging
from fastapi import Request, HTTPException, status, Header
from google.oauth2 import id_token
from google.auth.transport import requests

from app.utils.config import Settings

settings = Settings()


async def verify_token(request: Request, authorization: str = Header(None)):
    # For local development, bypass the authentication check.
    if settings.app_env == "local":
        logging.info("Skipping Pub/Sub authentication for local environment")
        return

    # Check if the middleware is configured correctly.
    if not settings.pubsub_base_url or not settings.pubsub_service_account_email:
        logging.error(
            "Pub/Sub auth middleware configured without an audience or expected email"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Configuration error: audience or email not set",
        )

    if not authorization:
        logging.warning("Missing Authorization header in Pub/Sub push request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: missing authorization header",
        )

    parts = authorization.split()

    if parts[0].lower() != "bearer" or len(parts) != 2:
        logging.warning("Malformed Authorization header in Pub/Sub push request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: malformed authorization header",
        )

    token = parts[1]
    audience = f"{settings.pubsub_base_url}{request.url.path}"

    try:
        decoded_token = id_token.verify_oauth2_token(
            token, requests.Request(), audience=audience
        )
    except ValueError as e:
        logging.error(f"Failed to validate Pub/Sub JWT: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid token",
        )

    email = decoded_token.get("email")
    if not email:
        logging.error("Email claim missing or invalid in Pub/Sub JWT")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: invalid email claim in token",
        )

    if not decoded_token.get("email_verified"):
        logging.error("Email claim is not verified in Pub/Sub JWT")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: email claim not verified",
        )

    if email != settings.pubsub_service_account_email:
        logging.warning(
            f"Pub/Sub JWT email does not match expected service account. "
            f"Got: {email}, Expected: {settings.pubsub_service_account_email}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: token email does not match expected service account",
        )

    logging.info(
        f"Successfully authenticated and authorized Pub/Sub push request for email: {email}"
    )
