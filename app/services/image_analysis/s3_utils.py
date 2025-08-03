import logging


def download_image(s3_client, bucket: str, key: str) -> bytes:
    """Downloads an image from S3."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        image_bytes = response["Body"].read()
        return image_bytes
    except Exception as e:
        logging.error(f"Failed to download image from s3://{bucket}/{key}: {e}")
        raise
