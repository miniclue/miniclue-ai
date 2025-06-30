from uuid import UUID
import json

import logging
from app.utils.config import Settings


settings = Settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s",
)


def chunk_text_by_tokens(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 200,
) -> list[tuple[str, int]]:
    import tiktoken

    encoder = tiktoken.encoding_for_model("gpt-4o-mini")
    all_tokens = encoder.encode(text)
    chunks: list[tuple[str, int]] = []
    step = chunk_size - overlap

    for i in range(0, len(all_tokens), step):
        # slice out the next chunk of token IDs
        chunk_tokens = all_tokens[i : i + chunk_size]
        # decode tokens back to text
        chunk_text_value = encoder.decode(chunk_tokens)
        token_count = len(chunk_tokens)
        chunks.append((chunk_text_value, token_count))
        if i + chunk_size >= len(all_tokens):
            break

    return chunks


async def ingest(lecture_id: UUID, storage_path: str):
    """Ingest PDF: download, parse slides, chunk text, process images, and enqueue embedding jobs"""
    logging.info(
        f"Starting ingestion for lecture_id={lecture_id}, storage_path={storage_path}"
    )
    # Fail if no Postgres DSN is configured
    if not settings.postgres_dsn:
        logging.error("Postgres DSN not configured")
        raise RuntimeError("Postgres DSN not configured")

    # Dynamic imports for heavy dependencies
    import boto3
    import asyncpg
    import pymupdf
    import pytesseract
    from PIL import Image
    import io
    import imagehash

    # Dynamic imports for BLIP
    try:
        from transformers import BlipProcessor, BlipForConditionalGeneration

        # Load BLIP processor and model
        blip_processor = BlipProcessor.from_pretrained(
            "Salesforce/blip-image-captioning-base", use_fast=True
        )
        blip_model = BlipForConditionalGeneration.from_pretrained(
            "Salesforce/blip-image-captioning-base"
        )
        blip_enabled = True
    except ImportError:
        blip_enabled = False

    logging.info(f"BLIP enabled: {blip_enabled}")

    def _process_image_content(
        img: Image.Image, log_identifier: str
    ) -> tuple[str, str]:
        """Helper to run OCR and BLIP on a PIL image."""
        ocr_text = pytesseract.image_to_string(img)

        alt_text = ""
        if blip_enabled:
            try:
                inputs = blip_processor(images=img, return_tensors="pt")
                out = blip_model.generate(**inputs)
                alt_text = blip_processor.decode(out[0], skip_special_tokens=True)
            except Exception as e:
                logging.warning(f"BLIP failed for {log_identifier}: {e}")
                pass
        return ocr_text, alt_text

    # Initialize S3 client
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        endpoint_url=settings.s3_endpoint_url or None,
    )
    logging.info("S3 client initialized")

    # Download PDF bytes
    logging.info(f"Downloading PDF from s3://{settings.s3_bucket_name}/{storage_path}")
    response = s3_client.get_object(Bucket=settings.s3_bucket_name, Key=storage_path)
    pdf_bytes = response["Body"].read()
    logging.info(f"PDF downloaded, size: {len(pdf_bytes)} bytes")

    # Open PDF in memory
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    total_slides = doc.page_count
    logging.info(f"PDF opened, total slides: {total_slides}")

    # Connect to Postgres
    logging.info("Connecting to Postgres...")
    conn = await asyncpg.connect(settings.postgres_dsn)
    logging.info("Postgres connection established")
    try:
        # Update lecture slide count
        await conn.execute(
            "UPDATE lectures SET total_slides=$1 WHERE id=$2",
            total_slides,
            str(lecture_id),
        )
        logging.info(f"Updated lecture {lecture_id} with total_slides={total_slides}")

        # In-memory registry for deduplication of content images
        content_registry: dict[str, str] = {}

        logging.info(f"Processing {total_slides} slides...")
        for page_index in range(total_slides):
            slide_number = page_index + 1
            page = doc.load_page(page_index)
            logging.info(f"Processing slide {slide_number}/{total_slides}")

            async with conn.transaction():
                # Insert slide record
                await conn.execute(
                    """
                    INSERT INTO slides
                      (lecture_id, slide_number, total_chunks, processed_chunks)
                    VALUES ($1, $2, 0, 0)
                    ON CONFLICT DO NOTHING
                    """,
                    str(lecture_id),
                    slide_number,
                )

                # Extract and chunk text
                raw_text = page.get_text()
                chunks = chunk_text_by_tokens(raw_text)
                total_chunks = len(chunks)
                logging.info(f"Slide {slide_number}: Created {total_chunks} chunks")

                # Update total_chunks
                await conn.execute(
                    """
                    UPDATE slides
                       SET total_chunks=$1
                     WHERE lecture_id=$2
                       AND slide_number=$3
                    """,
                    total_chunks,
                    str(lecture_id),
                    slide_number,
                )

                # Fetch slide_id
                row = await conn.fetchrow(
                    "SELECT id FROM slides WHERE lecture_id=$1 AND slide_number=$2",
                    str(lecture_id),
                    slide_number,
                )
                slide_id = row["id"]

                # Insert chunks and enqueue embedding jobs
                logging.info(
                    f"Inserting {total_chunks} chunks for slide {slide_number} and enqueuing embedding jobs..."
                )
                for idx, (text_chunk, token_count) in enumerate(chunks):
                    result = await conn.fetchrow(
                        """
                        INSERT INTO chunks
                          (slide_id, lecture_id, slide_number, chunk_index, text, token_count)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT DO NOTHING
                        RETURNING id
                        """,
                        slide_id,
                        str(lecture_id),
                        slide_number,
                        idx,
                        text_chunk,
                        token_count,
                    )
                    if result:
                        chunk_id = result["id"]
                    else:
                        chunk_id = await conn.fetchval(
                            "SELECT id FROM chunks WHERE slide_id=$1 AND chunk_index=$2",
                            slide_id,
                            idx,
                        )

                    # Enqueue embedding job
                    payload = {
                        "chunk_id": str(chunk_id),
                        "slide_id": str(slide_id),
                        "lecture_id": str(lecture_id),
                        "slide_number": slide_number,
                    }
                    await conn.execute(
                        "SELECT pgmq.send($1::text, $2::jsonb)",
                        settings.embedding_queue,
                        json.dumps(payload),
                    )

                # Extract embedded images
                images = page.get_images(full=True)
                if images:
                    logging.info(
                        f"Found {len(images)} images on slide {slide_number}, processing..."
                    )
                    for img_index, img_ref in enumerate(images):
                        xref = img_ref[0]
                        try:
                            info = doc.extract_image(xref)
                        except Exception as e:
                            logging.error(
                                f"Failed to extract image xref={xref} on slide {slide_number}: {e}"
                            )
                            continue
                        img_bytes = info["image"]
                        width = info["width"]
                        height = info["height"]
                        ext = info.get("ext", "png")
                        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

                        log_identifier = f"Image {img_index+1} on slide {slide_number}"
                        ocr_text, alt_text = _process_image_content(img, log_identifier)

                        # Compute perceptual hash
                        phash = str(imagehash.phash(img))

                        # Classify image
                        lower_alt_text = alt_text.lower()

                        # Keywords that strongly indicate a content-rich image
                        content_keywords = {
                            "diagram",
                            "chart",
                            "graph",
                            "table",
                            "screenshot",
                            "code",
                            "equation",
                            "map",
                            "plot",
                        }

                        # Keywords that often indicate a decorative image
                        decorative_keywords = {
                            "logo",
                            "icon",
                            "banner",
                            "background",
                            "illustration",
                            "photo",
                            "picture",
                            "drawing",
                            "artwork",
                            "decoration",
                        }

                        if any(kw in lower_alt_text for kw in content_keywords):
                            img_type = "content"
                        elif any(kw in lower_alt_text for kw in decorative_keywords):
                            img_type = "decorative"
                        elif ocr_text and len(ocr_text) >= 30:
                            img_type = "content"
                        elif (
                            alt_text
                            and len(alt_text.split()) >= 4
                            and len(alt_text) >= 30
                        ):
                            img_type = "content"
                        else:
                            img_type = "decorative"

                        logging.info(f"Image {img_index+1} classified as '{img_type}'")

                        # Upload or dedupe image
                        buffer = io.BytesIO()
                        img.save(buffer, format="PNG")
                        img_data = buffer.getvalue()

                        if img_type == "content":
                            if phash in content_registry:
                                img_path = content_registry[phash]
                            else:
                                img_key = f"lectures/{lecture_id}/slides/{slide_number}/raw_images/{img_index}.{ext}"
                                logging.info(
                                    f"Uploading new content image to S3 key: {img_key}"
                                )
                                s3_client.put_object(
                                    Bucket=settings.s3_bucket_name,
                                    Key=img_key,
                                    Body=img_data,
                                    ContentType=f"image/{ext}",
                                )
                                img_path = f"s3://{settings.s3_bucket_name}/{img_key}"
                                content_registry[phash] = img_path
                        else:
                            existing = await conn.fetchrow(
                                "SELECT storage_path FROM decorative_images_global WHERE image_hash=$1",
                                phash,
                            )
                            if existing:
                                img_path = existing["storage_path"]
                            else:
                                img_key = f"global/images/{phash}.png"
                                logging.info(
                                    f"Uploading new decorative image to S3 key: {img_key}"
                                )
                                s3_client.put_object(
                                    Bucket=settings.s3_bucket_name,
                                    Key=img_key,
                                    Body=img_data,
                                    ContentType="image/png",
                                )
                                img_path = f"s3://{settings.s3_bucket_name}/{img_key}"
                                await conn.execute(
                                    """
                                    INSERT INTO decorative_images_global(image_hash, storage_path)
                                    VALUES ($1, $2)
                                    ON CONFLICT DO NOTHING
                                    """,
                                    phash,
                                    img_path,
                                )

                        # Insert slide image metadata
                        await conn.execute(
                            """
                            INSERT INTO slide_images
                              (slide_id, lecture_id, slide_number, image_index, storage_path,
                               image_hash, type, ocr_text, alt_text, width, height)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                            ON CONFLICT DO NOTHING
                            """,
                            slide_id,
                            str(lecture_id),
                            slide_number,
                            img_index,
                            img_path,
                            phash,
                            img_type,
                            ocr_text,
                            alt_text,
                            width,
                            height,
                        )
                else:
                    logging.info(f"No images found on slide {slide_number}")

                # Render full slide for vector content to capture non-embedded graphics
                try:
                    # 2x zoom for better resolution
                    matrix = pymupdf.Matrix(2, 2)
                    pix = page.get_pixmap(matrix=matrix)
                    img_full = Image.frombytes(
                        "RGB", [pix.width, pix.height], pix.samples
                    )

                    log_identifier_full = f"Rendered slide {slide_number}"
                    ocr_full, alt_full = _process_image_content(
                        img_full, log_identifier_full
                    )

                    # Compute hash
                    phash_full = str(imagehash.phash(img_full))
                    # Upload full slide image
                    buffer_full = io.BytesIO()
                    img_full.save(buffer_full, format="PNG")
                    full_data = buffer_full.getvalue()
                    key_full = (
                        f"lectures/{lecture_id}/slides/{slide_number}/slide_image.png"
                    )
                    s3_client.put_object(
                        Bucket=settings.s3_bucket_name,
                        Key=key_full,
                        Body=full_data,
                        ContentType="image/png",
                    )
                    path_full = f"s3://{settings.s3_bucket_name}/{key_full}"
                    # Insert metadata for rendered slide
                    await conn.execute(
                        """
                        INSERT INTO slide_images
                          (slide_id, lecture_id, slide_number, image_index, storage_path,
                           image_hash, type, ocr_text, alt_text, width, height)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                        ON CONFLICT DO NOTHING
                        """,
                        slide_id,
                        str(lecture_id),
                        slide_number,
                        -1,
                        path_full,
                        phash_full,
                        "slide_image",
                        ocr_full,
                        alt_full,
                        pix.width,
                        pix.height,
                    )
                    logging.info(
                        f"Rendered full slide image uploaded for slide {slide_number}"
                    )
                except Exception as e:
                    logging.error(f"Failed to render full slide {slide_number}: {e}")
    finally:
        await conn.close()
        logging.info("Postgres connection closed")

    logging.info(f"Finished ingestion for lecture_id={lecture_id}")
