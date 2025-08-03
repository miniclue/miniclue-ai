def download_pdf(s3_client, bucket: str, key: str) -> bytes:
    response = s3_client.get_object(Bucket=bucket, Key=key)
    pdf_bytes = response["Body"].read()
    return pdf_bytes


def upload_image(s3_client, bucket: str, key: str, data: bytes, content_type: str):
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
