def download_pdf(s3_client, bucket: str, key: str) -> bytes:
    response = s3_client.get_object(Bucket=bucket, Key=key)
    pdf_bytes = response["Body"].read()
    return pdf_bytes


def upload_image(s3_client, bucket: str, key: str, data: bytes, content_type: str):
    """Uploads an image to S3. Raises an exception if the upload fails."""
    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    except Exception as e:
        import logging

        logging.error(
            f"Failed to upload image to S3: bucket={bucket}, key={key}, error={e}"
        )
        raise
