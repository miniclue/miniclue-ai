import json
import logging
from uuid import UUID

from google.cloud import pubsub_v1

from app.utils.config import Settings


settings = Settings()

if settings.pubsub_emulator_host:
    publisher = pubsub_v1.PublisherClient(
        client_options={"api_endpoint": settings.pubsub_emulator_host}
    )
else:
    publisher = pubsub_v1.PublisherClient()


def _publish_message(topic_name: str, data: dict):
    """Helper function to publish a message to a Pub/Sub topic."""
    if not settings.gcp_project_id:
        logging.error("GCP_PROJECT_ID is not set. Cannot publish message.")
        return

    topic_path = publisher.topic_path(settings.gcp_project_id, topic_name)
    message_data = json.dumps(data).encode("utf-8")

    try:
        future = publisher.publish(topic_path, message_data)
        message_id = future.result()
        logging.info(f"Published message {message_id} to {topic_path}.")
    except Exception as e:
        logging.error(f"Failed to publish message to {topic_path}: {e}")
        raise


def publish_embedding_job(lecture_id: UUID):
    """Publishes a job to the embedding topic."""
    if not settings.embedding_topic:
        logging.warning("EMBEDDING_TOPIC not set, skipping job submission.")
        return
    data = {"lecture_id": str(lecture_id)}
    _publish_message(settings.embedding_topic, data)
