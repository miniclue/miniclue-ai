import io
import logging
from uuid import UUID
from typing import Dict

import imagehash
import pymupdf
from PIL import Image
import asyncpg

from app.services.ingestion.db_utils import insert_slide_image
from app.services.ingestion.s3_utils import upload_image
from app.services.ingestion.pubsub_utils import publish_image_analysis_job
from app.utils.config import Settings

settings = Settings()


async def render_and_upload_slide_image(
    doc: pymupdf.Document,
    s3_client,
    conn: asyncpg.Connection,
    page_index: int,
    lecture_id: UUID,
    slide_id: UUID,
):
    """
    Renders a full-resolution image of a slide, uploads it to S3,
    and saves its metadata to the database.
    """
    slide_number = page_index + 1
    page = doc.load_page(page_index)
    storage_path = ""

    try:
        # Use a higher DPI for better quality
        matrix = pymupdf.Matrix(2, 2)
        pix = page.get_pixmap(matrix=matrix)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        phash = str(imagehash.phash(img))

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        img_data = buffer.getvalue()

        storage_key = f"lectures/{lecture_id}/slides/{slide_number}/full_slide.png"
        upload_image(
            s3_client, settings.s3_bucket_name, storage_key, img_data, "image/png"
        )
        storage_path = f"s3://{settings.s3_bucket_name}/{storage_key}"

        await insert_slide_image(
            conn,
            slide_id=slide_id,
            lecture_id=lecture_id,
            image_hash=phash,
            storage_path=storage_path,
            image_type="full_slide_render",
        )
        logging.info(f"Uploaded full rendered image for slide {slide_number}")

    except Exception as e:
        logging.error(
            f"Failed to render or upload full slide image for slide {slide_number}: {e}"
        )
        # Depending on requirements, you might want to re-raise or handle differently
    return storage_path


async def process_slide_sub_images(
    doc: pymupdf.Document,
    s3_client,
    conn: asyncpg.Connection,
    page_index: int,
    lecture_id: UUID,
    slide_id: UUID,
    processed_images_map: Dict[str, str],
):
    """
    Extracts all sub-images from a slide, uploads new ones, records them in the
    database, and publishes analysis jobs for the new ones.
    """
    page = doc.load_page(page_index)
    slide_number = page_index + 1
    images = page.get_images(full=True)

    if not images:
        logging.info(f"No sub-images found on slide {slide_number}")
        return

    logging.info(
        f"Found {len(images)} sub-images on slide {slide_number}, processing..."
    )
    for img_ref in images:
        xref = img_ref[0]
        try:
            img_info = doc.extract_image(xref)
            img_bytes = img_info["image"]
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            image_hash = str(imagehash.phash(img))

            storage_path = processed_images_map.get(image_hash)
            is_new_image = storage_path is None

            if is_new_image:
                # This is a new, unique image. Upload it.
                ext = img_info.get("ext", "png")
                storage_key = f"lectures/{lecture_id}/images/{image_hash}.{ext}"
                upload_image(
                    s3_client,
                    settings.s3_bucket_name,
                    storage_key,
                    img_bytes,
                    f"image/{ext}",
                )
                storage_path = f"s3://{settings.s3_bucket_name}/{storage_key}"
                processed_images_map[image_hash] = storage_path
                logging.info(
                    f"Uploaded new unique image with hash {image_hash} from slide {slide_number}"
                )

            # Create a record for this image instance on this specific slide
            slide_image_id = await insert_slide_image(
                conn,
                slide_id=slide_id,
                lecture_id=lecture_id,
                image_hash=image_hash,
                storage_path=storage_path,
                image_type=None,  # Type will be determined by the analysis service
            )

            if is_new_image:
                # Publish a job only for the newly uploaded image
                publish_image_analysis_job(
                    slide_image_id=slide_image_id,
                    lecture_id=lecture_id,
                    image_hash=image_hash,
                )

        except Exception as e:
            logging.error(
                f"Failed to process sub-image with xref {xref} on slide {slide_number}: {e}"
            )
            continue
