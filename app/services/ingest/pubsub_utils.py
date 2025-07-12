import json
import logging
from uuid import UUID

from google.cloud import pubsub_v1

from app.utils.config import Settings


settings = Settings()
publisher = pubsub_v1.PublisherClient()


def _publish_message(topic_name: str, data: dict):
    """Helper function to publish a message to a Pub/Sub topic."""
    if not settings.gcp_project_id:
        logging.error("GCP_PROJECT_ID is not set. Cannot publish message.")
        # In a real app, you might want to raise an exception
        # or have a more robust error handling mechanism.
        return

    topic_path = publisher.topic_path(settings.gcp_project_id, topic_name)
    message_data = json.dumps(data).encode("utf-8")

    try:
        future = publisher.publish(topic_path, message_data)
        message_id = future.result()
        logging.info(f"Published message {message_id} to {topic_path}.")
    except Exception as e:
        logging.error(f"Failed to publish message to {topic_path}: {e}")
        # Handle exception appropriately
        raise


def publish_image_analysis_job(slide_image_id: UUID, lecture_id: UUID, image_hash: str):
    """Publishes a job to the image-analysis topic."""
    if not settings.image_analysis_topic:
        logging.warning("IMAGE_ANALYSIS_TOPIC not set, skipping job submission.")
        return
    data = {
        "slide_image_id": str(slide_image_id),
        "lecture_id": str(lecture_id),
        "image_hash": image_hash,
    }
    _publish_message(settings.image_analysis_topic, data)


def publish_explanation_job(
    lecture_id: UUID,
    slide_id: UUID,
    slide_number: int,
    total_slides: int,
    slide_image_path: str,
):
    """Publishes a job to the explanation topic."""
    if not settings.explanation_topic:
        logging.warning("EXPLANATION_TOPIC not set, skipping job submission.")
        return
    data = {
        "lecture_id": str(lecture_id),
        "slide_id": str(slide_id),
        "slide_number": slide_number,
        "total_slides": total_slides,
        "slide_image_path": slide_image_path,
    }
    _publish_message(settings.explanation_topic, data)


def publish_embedding_job(lecture_id: UUID):
    """Publishes a job to the embedding topic."""
    if not settings.embedding_topic:
        logging.warning("EMBEDDING_TOPIC not set, skipping job submission.")
        return
    data = {"lecture_id": str(lecture_id)}
    _publish_message(settings.embedding_topic, data)
