import logging
from google.cloud import secretmanager
from google.api_core import exceptions

from app.utils.config import Settings

settings = Settings()

logger = logging.getLogger(__name__)


class SecretNotFoundError(Exception):
    """Raised when a secret is not found in Secret Manager."""

    pass


class SecretAccessError(Exception):
    """Raised when there's an error accessing a secret."""

    pass


class InvalidAPIKeyError(Exception):
    """
    Raised when an API key is invalid or missing.
    This is a permanent error that should not trigger Pub/Sub retries.
    """

    pass


def get_user_api_key(user_id: str, provider: str = "openai") -> str:
    """
    Fetches a user's API key from Google Cloud Secret Manager.

    Args:
        user_id: The user ID (customer_identifier) to fetch the key for
        provider: The API provider (default: "openai")

    Returns:
        The API key as a string

    Raises:
        SecretNotFoundError: If the secret doesn't exist
        SecretAccessError: If there's an error accessing the secret
    """
    project_id = settings.gcp_project_id
    if not project_id:
        raise SecretAccessError("GCP project ID is not configured")

    secret_name = f"user-{user_id}-{provider}-key"
    resource_name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"

    try:
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(request={"name": resource_name})
        api_key = response.payload.data.decode("UTF-8")
        return api_key
    except exceptions.NotFound:
        logger.error(f"Secret not found for user {user_id}")
        raise SecretNotFoundError(f"API key not found for user {user_id}")
    except Exception as e:
        logger.error(f"Error accessing secret for user {user_id}: {e}")
        raise SecretAccessError(f"Failed to access API key: {str(e)}")
