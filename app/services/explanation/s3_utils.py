import logging


def download_slide_image(s3_client, bucket: str, key: str) -> bytes:
    """
    Downloads the full-slide image from S3.
    """
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        image_bytes = response["Body"].read()
        return image_bytes
    except Exception as e:
        logging.error(f"Error downloading slide image '{key}': {e}")
        raise
