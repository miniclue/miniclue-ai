# Tech Stack

| Layer / Component                        | Technology & Notes                          |
| ---------------------------------------- | ------------------------------------------- |
| **Frontend**                             | Next.js (React)                             |
| • TipTap WYSIWYG editor                  |                                             |
| **API Gateway**                          | Golang (stlib)                              |
| • Route groups under `/v1` (see below)   |                                             |
| • JWT middleware validating Supabase JWT |                                             |
| **Auth**                                 | Supabase Auth (Google provider)             |
| **Object Storage**                       | Supabase Storage                            |
| **Relational & Vector**                  | Supabase Postgres (serverless)              |
| • pgvector (vector embeddings)           |                                             |
| **Message Queue**                        | Google Cloud Pub/Sub                        |
| **AI Microservices**                     | Python (FastAPI)                            |
| **PDF Parsing**                          | PyMuPDF                                     |
| **Embeddings**                           | OpenAI                                      |
| **LLM Inference**                        | OpenAI                                      |
| **Containerization**                     | Docker on Google Cloud Run                  |
| **CI/CD**                                | GitHub Actions                              |
| **Monitoring & Logging**                 | Supabase Logs / Sentry                      |
| **Cache (Later)**                        | Managed Redis (e.g. Upstash or Redis Cloud) |

# Repos

| Purpose                         | Type   | Deployment                    |
| ------------------------------- | ------ | ----------------------------- |
| Frontend                        | NextJS | Vercel Serverless             |
| Backend API Gateway             | Go     | Google Cloud Run (Serverless) |
| PDF processing and AI LLM calls | Python | Google Cloud Run (Serverless) |

# Pub/Sub Push-Based Workflow

We use Google Cloud Pub/Sub with push subscriptions to Python API endpoints:

- **Topics**: ingestion, embedding, explanation, summary
- **Subscriptions**: configured as push to `/{topic}` on your API server
- **Retry & Dead-Letter**: Each subscription has an exponential backoff policy (min:10s, max:10m). After exceeding max delivery attempts, failed messages are forwarded to a dead-letter topic, which pushes via HTTP POST to the `/dlq` endpoint on your API gateway. There, payloads are persisted in the database for logging and manual inspection.
- **Ack Deadline**: Configure each subscription's `ackDeadlineSeconds` to match your expected processing time (e.g., 60s), and use the client library's ack-deadline lease-extension API in long-running handlers to renew the deadline before it expires, preventing premature redelivery.
- **Handling Deleted Data (Defensive Subscribers)**: Pub/Sub does not support directly deleting specific in-flight messages. Instead, subscribers must be "defensive." Before processing a message, a subscriber should always query the database to confirm the associated lecture or entity still exists. If it has been deleted, the subscriber should simply acknowledge the message to prevent redelivery and take no further action. This approach is resilient to race conditions and simplifies the deletion logic in the main API.

# FastAPI Push Handlers

Base URL:

- Local: http://127.0.0.1:8000
- Staging: https://stg.svc.miniclue.com
- Production: https://svc.miniclue.com

/ingestion → Python ingestion endpoint
/embedding → Python embedding endpoint
/image-analysis → Python image analysis endpoint
/explanation → Python explanation endpoint
/summary → Python summary endpoint

Pub/Sub pushes directly to your Python services, which handle the entire async pipeline including status updates and publishes for downstream jobs.

# Go API Routes

Base URL:

- Local: http://127.0.0.1:8080
- Staging: https://stg.api.miniclue.com/v1
- Production: https://api.miniclue.com/v1

```
/api/v1/courses
├── POST / → create course
├── GET /:courseId → fetch course
├── PATCH /:courseId → update course
└── DELETE /:courseId → delete course

/api/v1/lectures
├── POST / → create lecture
├── GET / → list lectures (query by course_id) (`?limit=&offset=`)
├── GET /:lectureId → fetch lecture
├── PATCH /:lectureId → update lecture metadata
└── DELETE /:lectureId → delete lecture

/api/v1/lectures/:lectureId
├── GET /summary → get lecture summary
├── GET /explanations → list lecture explanations (`?limit=&offset=`)
├── GET /notes → get lecture notes
├── POST /notes → create lecture note
└── PATCH /notes → update lecture note

/api/v1/users/me
├── GET / → fetch user profile
├── POST / → create or update profile
├── GET /courses → list user's courses
└── GET /recents → list recent lectures (`?limit=&offset=`)
```

# Authentication

1. **Sign-in**
   - Next.js → Supabase Auth (Google OAuth) → issues a JWT.
   - JWT stored in a secure, HTTP-only cookie.
2. **API Gateway**
   - Every request to `/api/v1/*` carries the Supabase JWT.
   - Go middleware verifies token, enforces row-level security on `user_id`.

# AI Processing Design: Two Parallel Tracks

The new system is designed around two parallel processing tracks that start after the initial upload. This makes the system faster and more robust.

1.  **The Explanation Track (Fast Lane):** This track's only goal is to generate high-quality, slide-by-slide explanations for the user as quickly as possible. It uses slide images and a powerful AI to create the core value of the app.
2.  **The Search-Enrichment Track (Background Lane):** This track runs in the background. Its job is to meticulously extract all text, generate embeddings, and prepare the data for the future RAG-based chat feature. It's important, but it doesn't block the user from seeing results.

# Format of messages in topics

1.  ingestion

```
  {
  "lecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "storage_path": "lectures/55bdef4b-b9ac-4783-b8e4-87b47675333e/original.pdf"
  }
```

2. image-analysis

```
{
"slide_image_id": "f1e2d3c4-b5a6-7890-fedc-ba0987654321",
"lecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
"image_hash": "b432a1098fedcba"
}
```

3. embedding

```
{
  "lecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

4. explanation

```
{
"lecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
"slide_id": "c1b2a398-d4e5-f678-90ab-cdef12345678",
"slide_number": 5,
"total_slides": 30,
"slide_image_path": "lectures/a1b2.../slides/5.png"
}
```

5. summmary

```
{
  "lecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

# The Full Data Flow, Step-by-Step

## Step 1: User Uploads a Lecture

- **Trigger:** The user selects a PDF file and clicks "Upload" in the Next.js application.
- **Action:**
  1.  The request, containing the PDF file, is sent to your Go API Gateway.
  2.  The Go API immediately creates a new record in the `lectures` table with a status of `uploading`.
  3.  It then uploads the PDF file directly to Supabase Storage in a dedicated folder for that lecture.
  4.  Once the upload is successful, it updates the `lectures` record with the file's storage path and changes the status to `pending_processing`.
  5.  Finally, it publishes a single message to the Google Cloud Pub/Sub topic named `ingestion`. This message contains the unique ID of the lecture, kicking off the entire automated pipeline.

## Step 2: Ingestion and Dispatch Workflow

- **Trigger:** A message arrives from the `ingestion` topic, pushed to your Python API (`/ingestion`).
- **Action:** This service is now a fast, mechanical dispatcher. It makes no external AI calls.

  1.  It receives the lecture ID, **verifies the lecture exists in the database**, and updates its status to `parsing`.
  2.  It downloads the PDF and creates an in-memory dictionary called `processed_images_map`.
      - `processed_images_map = { image_hash -> storage_path }`
  3.  It processes the PDF **page by page**:

      - **Create Slide & Chunks**: Extracts raw_text, breaks it into chunks using tiktoken, and saves records to the slides and chunks tables.
      - It renders the high-resolution, full-page image for the main slide explanation and saves its record.
      - It finds all sub-images within the slide. For each sub-image:
        - **a. Compute Hash:** It computes the perceptual hash of the image.
        - **b. Check if Hash is New:** It checks if the hash exists as a key in the `processed_images_map`.
        - **c. If the Hash is NEW:**
          - Upload the image to storage to get a `new_storage_path`.
          - Add the entry to the map: `processed_images_map[hash] = new_storage_path`.
          - Create a new record in the `slide_images` table for the current slide, using the `hash` and the `new_storage_path`.
          - **Publish one `image-analysis` job** for this new record's ID.
        - **d. If the Hash is a DUPLICATE:**
          - Look up the existing path from the map: `existing_path = processed_images_map[hash]`.
          - Create a new record in the `slide_images` table for the current slide, using the `hash` and the `existing_path`.
          - **Do NOT publish another analysis job.**

  4.  **Save Final Counts**: After the loop, it saves the final total_sub_images count (the number of unique images) to the lectures table.
  5.  **Dispatch Explanation Jobs:** It loops through the slide data it collected and publishes a separate message to the `explanation` topic for **every single slide**.
  6.  **Handle No-Image Case: Checks if total_sub_images == 0. If so, it publishes the embedding job directly.**
  7.  **Finalize:** It updates the lecture status to `explaining` and returns a success signal.

## Step 3: Image Analysis

- **Trigger:** An `image-analysis` message arrives (only for unique images).
- **Action:** This handler performs the single, comprehensive AI analysis for each unique image.
  1.  It receives the `slide_images` ID, **first verifies the associated lecture exists**, then fetches the corresponding image from storage.
  2.  **Make One LLM Call:** It sends the image to your low-cost multi-modal LLM, asking for three pieces of information in a single, structured response: the image's `type` (`content` or `decorative`), its `ocr_text`, and its `alt_text`.
  3.  **Propagate Results:** It runs an update query on the `slide_images` table **where the `lecture_id` and `image_hash` match**. This ensures that the analysis results are written to _every single record_ representing that unique image across all slides in the lecture.
  4.  **The "Last Job" Logic:** In a single, atomic database transaction, it increments the `processed_sub_images` counter in the main `lectures` table and returns `processed_sub_images` and `total_sub_images`.
  5.  **Trigger Embedding Job (If Last):** If `processed_sub_images` == `total_sub_images`, it publishes the single embedding message containing the lecture_id.

## Step 4: Creating Searchable Embeddings

- **Trigger:** The single, large message arrives from the `embedding` topic.
- **Action:**
  1.  It receives the `lecture_id`, **first verifies the lecture still exists**, and then queries the database to get all the `chunks` for that lecture.
  2.  **Enrich the Text:** For each chunk, it builds a richer block of text. It looks up all images associated with the chunk's slide and **filters them to only include those where the `type` is `content`**. It then combines:
      - The original text of the chunk.
      - The `ocr_text` and `alt_text` from all relevant _content_ images.
  3.  **Generate Embeddings:** It sends all of these enriched text blocks to the OpenAI Embedding API in an efficient batch request.
  4.  **Save the Vectors:** The returned vectors are saved into the `embeddings` table, fully preparing the lecture for the future chat feature.
  5.  **Finalize Search-Enrichment Track**: After saving the vectors, it performs one final atomic transaction:
      - **SQL Logic**: UPDATE lectures SET embeddings_complete = TRUE WHERE id = :lecture_id RETURNING status;
      - **Application Logic**: It receives the current_status back. If current_status == 'summarising', it knows the other track has finished, so it runs a final UPDATE to set the lecture status to complete.

## Step 5: Generating Explanations

- **Trigger:** A message arrives from the `explanation` topic (one for each slide), running in parallel.
- **Action:**
  1.  **It first verifies the lecture exists.** It then checks if an explanation for this slide already exists and stops if it does.
  2.  **Gather Context:** It downloads the main slide image and queries the database for the raw text of the _previous_ and _next_ slides.
  3.  **Call the AI Professor:** It sends the slide image and the contextual text to your high-quality multi-modal LLM (like GPT-4o), asking for a detailed explanation, a one-liner summary, and the slide's purpose.
  4.  It saves the AI's structured response to the `explanations` table.
  5.  **Update Progress & Trigger Summary:** It safely increments the `processed_slides` counter. If this was the last slide (`processed_slides == total_slides`), it updates the lecture status to `summarising` and publishes the final message to the `summary` topic.

## Step 6: Creating the Final Lecture Summary

- **Trigger:** The final message arrives from the `summary` topic.
- **Action:**
  1.  **It first verifies the lecture exists.** It then checks if a summary for the lecture already exists and stops if so.
  2.  **Gather & Synthesize:** It gathers all the high-quality, slide-by-slide explanations from the database and sends them to the LLM one last time, asking it to synthesize a comprehensive "cheatsheet."
  3.  Finalize Explanation Track: After saving the summary, it performs one final atomic transaction:
      - **SQL Logic**: UPDATE lectures SET status = 'summarising' WHERE id = :lecture_id RETURNING embeddings_complete; (This confirms the status in case of retries).
      - **Application Logic**: It receives the embeddings_complete flag back. If the flag is true, it knows the other track has finished, so it runs a final UPDATE to set the lecture status to complete.
