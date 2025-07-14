import logging


def download_pdf(s3_client, bucket: str, key: str) -> bytes:
    logging.info(f"Downloading PDF from s3://{bucket}/{key}")
    response = s3_client.get_object(Bucket=bucket, Key=key)
    pdf_bytes = response["Body"].read()
    logging.info(f"PDF downloaded, size: {len(pdf_bytes)} bytes")
    return pdf_bytes


def upload_image(s3_client, bucket: str, key: str, data: bytes, content_type: str):
    logging.info(f"Uploading image to S3 key: {key}")
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
