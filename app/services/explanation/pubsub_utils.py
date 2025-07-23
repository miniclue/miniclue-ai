import json
import logging
from uuid import UUID
from typing import Optional

from google.cloud import pubsub_v1

from app.utils.config import Settings


settings = Settings()

if settings.pubsub_emulator_host:
    publisher = pubsub_v1.PublisherClient(
        client_options={"api_endpoint": settings.pubsub_emulator_host}
    )
else:
    publisher = pubsub_v1.PublisherClient()


def publish_summary_job(
    lecture_id: UUID,
    customer_identifier: str,
    name: Optional[str],
    email: Optional[str],
):
    """
    Publishes a job to the summary topic with customer tracking.
    """
    if not settings.summary_topic:
        logging.warning("SUMMARY_TOPIC not set, skipping summary job submission.")
        return

    if not settings.gcp_project_id:
        logging.error("GCP_PROJECT_ID is not set. Cannot publish message.")
        raise ValueError("GCP_PROJECT_ID is not configured.")

    topic_path = publisher.topic_path(settings.gcp_project_id, settings.summary_topic)
    message_data = json.dumps(
        {
            "lecture_id": str(lecture_id),
            "customer_identifier": customer_identifier,
            "name": name,
            "email": email,
        }
    ).encode("utf-8")

    try:
        future = publisher.publish(topic_path, message_data)
        message_id = future.result()
        logging.info(
            f"Published summary job for lecture {lecture_id}, message_id: {message_id}"
        )
    except Exception as e:
        logging.error(f"Failed to publish summary job for lecture {lecture_id}: {e}")
        raise
