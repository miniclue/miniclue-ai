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
/v1/courses
├── POST / → create course
├── GET /:courseId → fetch course
├── PATCH /:courseId → update course
└── DELETE /:courseId → delete course

/v1/lectures
├── POST / → upload lecture (multipart form)
├── GET / → list lectures (query by course_id) (`?limit=&offset=`)
├── GET /:lectureId → fetch lecture
├── PATCH /:lectureId → update lecture metadata
└── DELETE /:lectureId → delete lecture

/v1/lectures/:lectureId
├── GET /summary → get lecture summary
├── GET /explanations → list lecture explanations (`?limit=&offset=`)
├── GET /note → get lecture note
├── POST /note → create lecture note
└── PATCH /note → update lecture note

/v1/lectures/:lectureId/url
└── GET / → get signed URL for lecture file

/v1/users/me
├── GET / → fetch user profile
├── POST / → create or update profile
├── GET /courses → list user's courses
├── GET /recents → list recent lectures (`?limit=&offset=`)
└── GET /usage → get user usage statistics
└── GET /subscriptions → get user's active subscription

/v1/subscriptions
├── POST /checkout → create Stripe checkout session
└── GET /portal → create Stripe customer portal session

/v1/stripe/webhooks
└── POST / → handle Stripe webhook events

/v1/dlq
└── POST / → handle dead-letter queue messages (Pub/Sub push)
```

# Pricing

**Free:**

- Cost: $0/mo
- 3 lecture uploads per month
- Max 10 MB per lecture
- Limited chat usage

**Beta:**

- Cost: $0/mo
- Unlimited lecture uploads per month (100 lecture uploads per month)
- Max 300 MB per lecture
- Unlimited chat usage
- Beta pricing will only end once beta is over

**Pro (Monthly) - Launch Offer:**

- Cost: $10/mo
- Unlimited lecture uploads per month
- Max 300 MB per lecture
- Unlimited chat usage
- Price will increase to $20/month as new features (tutorial explainers, flashcards, MCQ quiz) are added

**Pro (Annual) - Launch Offer:**

- Cost: $6/mo ($72/yr)
- Same benefits as Pro (Monthly)
- Price will increase to $12/mo ($144/yr) as new features (tutorial explainers, flashcards, MCQ quiz) are added

**Pro (Monthly):**

- Cost: $20/mo
- Unlimited lecture uploads per month
- Max 300 MB per lecture
- Unlimited chat usage

**Pro (Annual):**

- Cost: $12/mo ($144/yr)
- Same benefits as Pro (Monthly)

# Subscription Lifecycle

This section outlines how user subscriptions are managed, renewed, and synchronized with our billing provider (Stripe).

## Free & Beta Plan Lifecycle (Automated via `pg_cron`)

Free-tier renewals are handled automatically by a daily database job, ensuring users get a fresh quota each month without manual intervention.

- **Onboarding:** New users are automatically subscribed to the `beta` plan upon their first sign-in. This is handled by the Go API's `user_service`, which idempotently creates a subscription record `ON CONFLICT DO NOTHING`.
- **Active Period:** A subscription is considered active for its defined period (e.g., 31 days).
- **The "Expired Gap" & Mitigation:** A brief window could exist where a user's plan is technically expired before the daily renewal job runs. This is mitigated by a **6-hour grace period** in the `GetActiveSubscription` database query, which treats a recently expired subscription as active. This ensures the renewal is seamless from a user's perspective.
- **Daily Renewal (`pg_cron`):** A scheduled job (`cron.schedule`) runs once every 24 hours. It finds all `'active'` subscriptions for the `'free'` and `'beta'` plans that have passed their `ends_at` date and rolls their billing period forward.

## Paid Plan Lifecycle (Driven by Stripe Webhooks)

Stripe is the source of truth for all paid plans. Our database simply mirrors the state provided by Stripe's webhook events.

1.  **Upgrade to Paid:**

    - A user initiates an upgrade in the UI and is redirected to a **Stripe Checkout** session.
    - Upon successful payment, Stripe sends a `checkout.session.completed` webhook to our Go API.
    - The webhook handler creates or updates the user's record in `user_subscriptions` with the new `plan_id` and the `ends_at` timestamp provided by Stripe.

2.  **Automatic Renewals:**

    - Stripe automatically handles recurring billing based on the plan's interval (monthly or yearly).
    - On a successful charge, Stripe sends an `invoice.payment_succeeded` webhook.
    - Our handler updates the `ends_at` timestamp to reflect the new billing period.

3.  **User-Initiated Cancellation:**

    - The user cancels their plan via the **Stripe Customer Portal**.
    - Stripe sends a `customer.subscription.updated` webhook, indicating the subscription will `cancel_at_period_end`.
    - Our handler updates the subscription `status` to `'cancelled'`. The user retains full access until their paid period ends.

4.  **Downgrade to Free (at Period End):**

    - When the paid period officially ends, Stripe sends a `customer.subscription.deleted` webhook.
    - Our handler receives this and updates the user's subscription: `plan_id` is set back to `'free'`, `status` becomes `'active'`, and a new 31-day period begins.

5.  **Failed Payments (Dunning):**
    - If a recurring payment fails, Stripe sends an `invoice.payment_failed` webhook.
    - Our handler updates the user's subscription `status` to `'past_due'`.
    - Our API middleware will block lecture upload for any user whose status is not `'active'` or `'cancelled'`.

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

1. ingestion

```json
{
  "lecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "storage_path": "lectures/55bdef4b-b9ac-4783-b8e4-87b47675333e/original.pdf",
  "customer_identifier": "customer_1",
  "name": "Hendrix Liu",
  "email": "hendrix@keywordsai.co"
}
```

2. image-analysis

```json
{
  "slide_image_id": "f1e2d3c4-b5a6-7890-fedc-ba0987654321",
  "lecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "image_hash": "b432a1098fedcba",
  "customer_identifier": "customer_1",
  "name": "Hendrix Liu",
  "email": "hendrix@keywordsai.co"
}
```

3. embedding

```json
{
  "lecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "customer_identifier": "customer_1",
  "name": "Hendrix Liu",
  "email": "hendrix@keywordsai.co"
}
```

4. explanation

```json
{
  "lecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "slide_id": "c1b2a398-d4e5-f678-90ab-cdef12345678",
  "slide_number": 5,
  "total_slides": 30,
  "slide_image_path": "lectures/a1b2.../slides/5.png",
  "customer_identifier": "customer_1",
  "name": "Hendrix Liu",
  "email": "hendrix@keywordsai.co"
}
```

5. summary

```json
{
  "lecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "customer_identifier": "customer_1",
  "name": "Hendrix Liu",
  "email": "hendrix@keywordsai.co"
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
- **Action:** This service is a fast, mechanical dispatcher. It makes no external AI calls. Its modern implementation includes key improvements for robustness and data integrity.

  1.  **Preparation**: It receives the lecture ID and connects to the database.
  2.  **Verification & Setup**: It **verifies the lecture exists (a defensive subscriber pattern)**, and **clears any previous error details** from the `lectures` table. This ensures that retries start from a clean state. It then downloads the PDF from storage, and updates the lecture status to `parsing` while saving the total slide count.
  3.  **Page-by-Page Processing Loop**: It processes the PDF one page at a time. Each page's processing is wrapped in its own **atomic database transaction** to ensure data integrity.
      - **Create Slide & Chunks**: It extracts raw text and creates records for the slide and its text chunks.
      - **Render Main Image**: It renders the high-resolution, full-page image for the main slide explanation and saves its record.
      - **Process Sub-Images**: It finds all sub-images within the slide, computing a hash for each one.
        - It uses an in-memory map (`processed_images_map`) to track unique images.
        - If an image is new, it's uploaded, its details are added to the map, a new `slide_images` record is created, and an `image-analysis` job is added to an in-memory list.
        - If an image is a duplicate, a `slide_images` record is created using the existing path, and no new job is dispatched.
  4.  **Batch Dispatch**: After the loop finishes, it performs its dispatching operations.
      - It saves the final count of unique sub-images (`total_sub_images`) to the `lectures` table.
      - It publishes an `explanation` job for **every single slide**.
      - It publishes all the collected `image-analysis` jobs at once.
      - **Handle No-Image Case**: If `total_sub_images == 0`, it publishes the `embedding` job directly.
  5.  **Finalize**: It updates the lecture status to `explaining`.
  6.  **Robust Error Handling**: The entire process is wrapped in a `try/except` block. If any error occurs, the lecture status is set to `failed` with detailed error information, and the exception is re-raised to ensure the message is not lost.

## Step 3: Image Analysis

- **Trigger:** An `image-analysis` message arrives (only for unique images).
- **Action:** This handler performs a single AI analysis for each unique image, with improved observability and transaction management.

  1.  **Verification**: It receives the `slide_images` ID, **first verifies the associated lecture exists**, then fetches the corresponding image from storage.
  2.  **Make One LLM Call**: It sends the image to a multi-modal LLM, asking for the image's `type` (`content` or `decorative`), its `ocr_text`, and its `alt_text`. The implementation also includes a **mocking flag** to bypass the real LLM call for testing.
  3.  **Atomic Updates**: It uses a **tightly-scoped, atomic database transaction** for all write operations to ensure data consistency and minimize lock times.
      - **Propagate Results:** It runs an `UPDATE` query on the `slide_images` table **where the `lecture_id` and `image_hash` match**. This ensures the analysis is written to every record representing that unique image.
      - **The "Last Job" Logic:** It increments the `processed_sub_images` counter in the main `lectures` table.
  4.  **Trigger Embedding Job (If Last):** After the transaction is successfully committed, it checks if `processed_sub_images == total_sub_images`. If they match, it publishes the single `embedding` message.
  5.  **Granular Error Handling**: If an error occurs, it is caught, and a structured JSON error object is written to a dedicated `search_error_details` field in the `lectures` table before the exception is re-raised to trigger a Pub/Sub retry.

## Step 4: Creating Searchable Embeddings

- **Trigger:** The single message arrives from the `embedding` topic.
- **Action:** This service is highly optimized for performance and correctness.

  1.  **Verification**: It receives the `lecture_id` and **verifies the lecture still exists**.
  2.  **Efficient Data Fetching**: It queries the database to get all `chunks` and all content-rich `slide_images` for the entire lecture in **two efficient, bulk queries**, avoiding the N+1 problem. It then uses an in-memory dictionary for fast lookups.
  3.  **Handle No-Text Case**: It gracefully handles the edge case where a lecture contains no text chunks, logs a warning, and proceeds to finalize the track to unblock the pipeline.
  4.  **Enrich the Text**: For each chunk, it builds a richer block of text by combining the original chunk text with the `ocr_text` and `alt_text` from its associated content images. It adds explicit labels like `"OCR Text:"` to provide better semantic context for the embedding model.
  5.  **Generate Embeddings**: It sends all enriched text blocks to the OpenAI Embedding API in an efficient batch request. A mocking flag is also supported.
  6.  **Atomic Finalization**: The entire finalization process occurs within a **single atomic transaction**.
      - **Batch Upsert**: The returned vectors are saved to the `embeddings` table using a **performant batch `UPSERT` operation**, which is both fast and idempotent.
      - **Finalize Search-Enrichment Track**: It sets `embeddings_complete = TRUE` and retrieves the lecture's current `status`.
      - **Rendezvous Check**: If the `status` is already `summarising`, it knows the other track has finished, so it runs a final `UPDATE` to set the lecture status to `complete`.
  7.  **Error Handling**: On failure, the lecture status is set to `failed` with error details.

## Step 5: Generating Explanations

- **Trigger:** A message arrives from the `explanation` topic (one for each slide), running in parallel.
- **Action:** This service is designed for idempotency and clear separation of concerns.

  1.  **Verification**: **It first verifies the lecture exists** and then checks if an explanation for this slide already exists, skipping if it does to ensure idempotency.
  2.  **Gather Context**: It downloads the main slide image and queries the database for the raw text of the _previous_ and _next_ slides.
  3.  **Call the AI Professor**: It sends the slide image and context to a high-quality multi-modal LLM. This also supports a mocking flag for testing.
  4.  **Save Results**: It saves the AI's structured response to the `explanations` table.
  5.  **Atomic Progress Update & Trigger**: In a **single atomic database operation**, it increments the `processed_slides` counter and checks if this was the last slide.
      - If it was the last slide, it **only publishes the final message to the `summary` topic**. It does _not_ change the lecture's status itself, deferring that responsibility to the summary service for better resilience.
  6.  **Track-Specific Error Handling**: On failure, it writes a structured error to a dedicated `explanation_error_details` field, allowing for clear distinction between failures in the explanation track versus the search track.

## Step 6: Creating the Final Lecture Summary

- **Trigger:** The final message arrives from the `summary` topic.
- **Action:** This service acts as the final arbiter of the explanation track and the overall lecture status.

  1.  **Verification**: **It first verifies the lecture exists** and checks if a summary already exists to ensure idempotency. It also gracefully handles the case where no explanations were found for the lecture.
  2.  **Gather & Synthesize**: It gathers all the slide-by-slide explanations and sends them to the LLM to synthesize a comprehensive "cheatsheet."
  3.  **Atomic Finalization**: The final database updates occur within a **single atomic transaction**:
      - It saves the summary to the database.
      - It idempotently updates the lecture `status` to `summarising`.
      - It then fetches the `embeddings_complete` flag to see if the other track is finished.
  4.  **Final Rendezvous**: After the transaction commits, it checks the `embeddings_complete` flag. If `true`, it knows the other track has finished, so it runs a final `UPDATE` to set the lecture `status` to `complete`.
  5.  **Error Handling**: On failure, it re-raises the exception to leverage Pub/Sub's automatic retry mechanism. If all retries fail, the message is sent to the dead-letter queue for manual inspection.
