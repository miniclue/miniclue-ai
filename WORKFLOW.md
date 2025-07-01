# Tech Stack

| Layer / Component                          | Technology & Notes                              |
| ------------------------------------------ | ----------------------------------------------- |
| **Frontend**                               | Next.js (React)                                 |
| • TipTap WYSIWYG editor                    |                                                 |
| **API Gateway**                            | Golang (stlib)                                  |
| • Route groups under `/api/v1` (see below) |                                                 |
| • JWT middleware validating Supabase JWT   |                                                 |
| **Auth**                                   | Supabase Auth (Google provider)                 |
| **Object Storage**                         | Supabase Storage                                |
| **Relational & Vector**                    | Supabase Postgres (serverless)                  |
| • pgvector (vector embeddings)             |                                                 |
| • pgmq (Supabase Queues)                   |                                                 |
| **Message Queue**                          | Supabase Queues (pgmq)                          |
| **AI Microservices**                       | Python (FastAPI) on Vercel/Fly.io               |
| **PDF Parsing**                            | PyMuPDF or PDFMiner                             |
| **Embeddings**                             | OpenAI / Claude / Gemini Embedding APIs         |
| **LLM Inference**                          | OpenAI GPT-4o / Claude / Gemini                 |
| **Containerization**                       | Docker (for Python services) on Vercel / Fly.io |
| **CI/CD**                                  | GitHub Actions                                  |
| **Monitoring & Logging**                   | Supabase Logs → Grafana / DataDog               |
| **Cache (Later)**                          | Managed Redis (e.g. Upstash or Redis Cloud)     |

# Repos

| Purpose                         | Type   | Deployment                          | Remarks      |
| ------------------------------- | ------ | ----------------------------------- | ------------ |
| Frontend                        | NextJS | Vercel Serverless                   |              |
| Backend API Gateway             | Go     | Google Cloud Run (Serverless)       | Same Go Repo |
| Backend worker service          | Go     | Google Cloud Run (min. 1 instance)  | Same Go Repo |
| PDF processing and AI LLM calls | Python | Google Cloud Run, Fly.io Serverless |              |

<aside>
💡

Rationale:

1. Separate worker service and PDF processing because worker service needs to constantly poll pgmq and cannot be deployed serverless
2. Choice of Go instead of Python for backend worker service is because it is easier to write, and it is also smaller and cheaper to run
</aside>

## Worker Service Modes

The Go worker binary supports four modes:

- `ingestion`: Polls `ingestion_queue` and processes ingestion jobs.
- `embedding`: Polls `embedding_queue` and processes embedding jobs.
- `explanation`: Polls `explanation_queue` and processes explanation jobs.
- `summary`: Polls `summary_queue` and processes summary jobs.

### Building and Running the Worker

First build the worker binary:

- make build-orchestrator

Then run a specific mode:

- make run-orchestrator-ingestion
- make run-orchestrator-embedding
- make run-orchestrator-explanation
- make run-orchestrator-summary

# Key API Route Groups

```
/api/v1/courses
├── POST / → create course
├── GET /:courseId → fetch course
├── PUT /:courseId → update course
└── DELETE /:courseId → delete course

/api/v1/lectures
├── POST / → create lecture
├── GET / → list lectures (query by course_id) (`?limit=&offset=`)
├── GET /:lectureId → fetch lecture
├── PUT /:lectureId → update lecture metadata
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

# Data Flow

## 3.1. Client Upload → Go API

1. **Request**

   ```
   POST /api/v1/lectures
   Content-Type: multipart/form-data
   Body: { file: <PDF>, metadata… }
   ```

2. **Go API Handler**
   - Create lecture record with status `uploading`
   - Store PDF in Supabase Storage at `lectures/{lectureId}/original.pdf`.
   - Store S3 URL in database
   - Update status to `pending_processing`
   - Enqueue a job on `ingestion_queue` with payload `{ lecture_id, storage_path }`.
   - On error, roll back DB and/or enqueue a cleanup job.

---

## 3.2. Ingestion Orchestrator (Go)

**Trigger:** new message on `ingestion_queue`

1. **Poll & Receive**
   - Go worker does a long-poll: `pgmq.read_with_poll('ingestion_queue', …)` → `{ lecture_id, storage_path }`.
2. Update lecture `status` to parsing
3. **Call Python Ingestion Service**

   ```
   POST http://python-ai/ingest
   Content-Type: application/json
   Body: { "lecture_id": …, "storage_path": … }
   ```

4. **Ack or Retry**
   - On HTTP 200: Go worker `DELETE` the message from `ingestion_queue` and emit metrics. UPDATE `lectures.status = 'embedding'` and `updated_at = NOW()`.
   - **Error Handling**: let the Go orchestrator retry with exponential backoff; on repeated failures, move the job to your DLQ, update `lectures.status = 'failed'` and set `lectures.error_message`

### 3.2.1 Python Ingestion Service

1. **Initialize Dependencies & Configuration**

   - Dynamic imports for heavy dependencies: `boto3`, `asyncpg`, `pymupdf`, `pytesseract`, `PIL`, `imagehash`
   - Optional BLIP model loading for image captioning (Salesforce/blip-image-captioning-base)
   - Initialize S3 client with Supabase Storage credentials
   - Connect to Postgres using asyncpg

2. **Download & Open PDF**

   - Fetch PDF bytes from Supabase Storage using S3 client
   - Open PDF in memory with PyMuPDF: `pymupdf.open(stream=pdf_bytes, filetype="pdf")`
   - Update `lectures.total_slides = doc.page_count`

3. **Initialize Content Registry**

   - Create in-memory registry: `content_registry: dict[str, str] = {}` for deduplication

4. **Process Each Slide (Page)**

   For each `page_index` in range(`total_slides`):

   **4.1. Slide Setup**

   - `slide_number = page_index + 1`
   - Insert slide record with transaction:
     ```sql
     INSERT INTO slides (lecture_id, slide_number, total_chunks, processed_chunks)
     VALUES ($1, $2, 0, 0)
     ON CONFLICT DO NOTHING
     ```

   **4.2. Text Processing**

   - Extract text: `raw_text = page.get_text()`
   - Chunk text using tiktoken (cl100k_base encoding):
     - Chunk size: 1000 tokens
     - Overlap: 200 tokens
     - Step: 800 tokens
   - Update `slides.total_chunks` with actual chunk count
   - Insert chunks and enqueue embedding jobs:
     ```sql
     INSERT INTO chunks (slide_id, lecture_id, slide_number, chunk_index, text, token_count)
     VALUES ($1, $2, $3, $4, $5, $6)
     ON CONFLICT DO NOTHING
     ```
   - Enqueue embedding job via pgmq:
     ```sql
     SELECT pgmq.send($1::text, $2::jsonb)
     ```

   **4.3. Image Processing**

   **4.3.1. Extract Embedded Images**

   - Get images: `page.get_images(full=True)`
   - For each image reference:
     - Extract image data: `doc.extract_image(xref)`
     - Convert to PIL Image for processing

   **4.3.2. Image Classification & Processing**

   - **OCR**: Run Tesseract: `pytesseract.image_to_string(img)`
   - **Alt-text**: Run BLIP (if enabled): Generate caption using transformers
   - **Hash**: Compute perceptual hash: `imagehash.phash(img)`
   - **Classification Logic**:
     - Content keywords: diagram, chart, graph, table, screenshot, code, equation, map, plot
     - Decorative keywords: logo, icon, banner, background, illustration, photo, picture, drawing, artwork, decoration
     - If OCR ≥30 chars OR (alt-text ≥4 words AND ≥30 chars) → content
     - If decorative keywords present → decorative
     - Default: decorative

   **4.3.3. Image Storage & Deduplication**

   - **Content Images**:
     - Check `content_registry[phash]` for existing path
     - If new: upload to `lectures/{lecture_id}/slides/{slide_number}/raw_images/{img_index}.{ext}`
     - Store path in registry
   - **Decorative Images**:
     - Query `decorative_images_global` for existing hash
     - If new: upload to `global/images/{phash}.png`
     - Insert into global registry
   - **Metadata Storage**:
     ```sql
     INSERT INTO slide_images (slide_id, lecture_id, slide_number, image_index,
                              storage_path, image_hash, type, ocr_text, alt_text, width, height)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
     ON CONFLICT DO NOTHING
     ```

   **4.4. Full Slide Rendering**

   - Render complete slide at 2x zoom: `page.get_pixmap(matrix=pymupdf.Matrix(2, 2))`
   - Process rendered slide with OCR and BLIP
   - Upload to `lectures/{lecture_id}/slides/{slide_number}/slide_image.png`
   - Store metadata with `image_index = -1` and `type = 'slide_image'`

5. **Error Handling & Reliability**
   - Each slide processed in database transaction
   - Comprehensive logging throughout process
   - Graceful handling of BLIP failures
   - Idempotent operations with `ON CONFLICT DO NOTHING`
   - Proper connection cleanup in finally block

---

## 3.3. Embedding Orchestrator (Go)

**Trigger:** new message on `embedding_queue`

1. **Poll & Receive**

   Read a job from `embedding_queue`, which now carries:

   ```json
   {
     "chunk_id": "...",
     "slide_id": "...",
     "lecture_id": "...",
     "slide_number": 3
   }
   ```

2. **Call Python Embedding Service**

   ```
   POST http://python-ai/embed
   Content-Type: application/json

   {
     "chunk_id":     "...",
     "slide_id": "...",
     "lecture_id":   "...",
     "slide_number": 3
   }
   ```

3. **Ack or Retry**
   - On HTTP 200: `DELETE` the message from `embedding_queue`, emit success metrics.
   - **Error Handling**: let the Go orchestrator retry with exponential backoff; on repeated failures, move the job to your DLQ, update `lectures.status = 'failed'` and set `lectures.error_message`

### **3.3.1 Python Embedding Service**

1. **Parse Payload**

   Extract `chunk_id`, `lecture_id`, and `slide_number` from the request body.

2. **Fetch Chunk & Embed**

   - `SELECT text FROM chunks WHERE id = :chunk_id`
   - Call your embedding API (OpenAI) to get a vector.
   - **UPSERT** into the `embeddings` table:

     ```sql
     INSERT INTO embeddings
       (chunk_id, slide_id, lecture_id, slide_number, vector, metadata)
     VALUES
       (:chunk_id, :slide_id, :lecture_id, :slide_number, :vector, :meta)
     ON CONFLICT (chunk_id) DO UPDATE
       SET vector = EXCLUDED.vector,
           updated_at = NOW();

     ```

3. **Update Slide Progress and check if all chunks are embedded for that slide**

   ```sql
   -- Atomically update and get the new values
   WITH updated AS (
     UPDATE slides
        SET processed_chunks = processed_chunks + 1
      WHERE id = :slide_id
     RETURNING processed_chunks, total_chunks
   )
   SELECT processed_chunks, total_chunks FROM updated;
   ```

4. If `processed_chunks = total_chunks`, send a new job:

   ```
   pgmq.send("explanation_queue", {
     "slide_id": "...",
     "lecture_id":   "...",
     "slide_number": 3
   })
   ```

5. **Update lecture status if all slides are embedded for that lecture**

   1. Check if processed_chunks = total_chunks for all slides
   2. If yes, update lecture `status` to `explaining`

   ```jsx
   SELECT COUNT(*)
     FROM slides
    WHERE lecture_id = :lecture_id
      AND processed_chunks < total_chunks;
   ```

6. **Emit Metrics & Ack**

   Record token usage and timing, then respond HTTP 200.

---

## 3.4. Explanation Orchestrator (Go)

**Trigger:** new message on `explanation_queue`

Payload:

```json
{
  "slide_id": "...",
  "lecture_id":   "...",
  "slide_number": N
}
```

1. **Poll & Receive**

   Read the job off the queue.

2. **Wait for Previous Explanation**

   ```sql
   SELECT 1
     FROM explanations
    WHERE lecture_id   = :lecture_id
      AND slide_number = :slide_number - 1;

   ```

   If `slide_number > 1` and no row, return non-200 (NACK) so the orchestrator retries with backoff.

3. **Call Python Explanation Service**

   ```
   POST http://python-ai/explain
   Content-Type: application/json

   {
     "slide_id": "...",
     "lecture_id":   "...",
     "slide_number": N
   }
   ```

4. **Ack or Retry**
   - On HTTP 200: delete message, emit success metrics.
   - **Error Handling**: let the Go orchestrator retry with exponential backoff; on repeated failures, move the job to your DLQ, update `lectures.status = 'failed'` and set `lectures.error_message`

---

### 3.4.1 Python Explanation Service

1. **Parse Payload**

   Read `lecture_id` and `slide_number`.

2. **Fetch Context in One Go**

   ```sql
   SELECT slide_number, one_liner
     FROM explanations
    WHERE lecture_id   = :lecture_id
      AND slide_number < :slide_number
    ORDER BY slide_number DESC
    LIMIT 3;

   ```

   ```python
   rows = db.fetch_all(...)
   if rows:
       previousOneLiner = rows[0].one_liner
   contextRecap = [r.one_liner for r in rows]  # up to 3

   ```

3. **Fetch Current Slide Data**

   - **Chunks**:

     ```sql
     SELECT text
       FROM chunks
      WHERE lecture_id   = :lecture_id
        AND slide_number = :slide_number
      ORDER BY chunk_index;

     ```

   - **Images**: non-decorative `ocr_text`/`alt_text` from `slide_images`.

4. **Fetch Related Concepts (Partial Context)**

   ```sql
   -- build query vector from combined current-chunk text
   SELECT text
     FROM embeddings
    WHERE lecture_id = :lecture_id
   ORDER BY vector <-> :query_vector
   LIMIT K;

   ```

5. **Prompt Assembly & API Call**

   ```python
   import openai

   # 1. System message defines roles and instructions
   system_msg = """
   You are an AI teaching assistant specialized in university lectures.
   For each slide, you will:
   1. Judge whether it is:
      • an **Introduction** (lecture cover),
      • a **Transition** (section header), or
      • a **Content** slide.
   2. Based on that classification:
      - For **Introduction**: output a one-liner overview of the lecture and a brief paragraph on its importance.
      - For **Transition**: output a one-liner preview of the upcoming topic and a single-sentence transition.
      - For **Content**: output a one-liner key takeaway and a detailed explanation using the Minto Pyramid structure (point → supporting details).
   3. Always explain jargon, write in plain English, and include emojis and rhetorical questions sparingly.
   4. Do **NOT** preview future slides.
   5. Return **valid JSON** exactly with two fields:
      `{ "one_liner": "...", "content": "..." }`
   """

   # 2. Build the user prompt with slide data
   user_prompt = f"""
   Slide #{slide_number}
   Previous takeaway: "{previous_one_liner}"
   Context recap (last up to 3): {', '.join(context_recap)}

   Text chunks:
   {chr(10).join(current_chunks)}

   Image OCR texts:
   {', '.join(ocr_texts)}

   Image alt texts:
   {', '.join(alt_texts)}

   Now follow the system instructions above.
   """

   # 3. Call OpenAI
   response = openai.responses.create(
       model="gpt-4o",
       messages=[
           {"role": "system", "content": system_msg},
           {"role": "user",   "content": user_prompt}
       ],
       temperature=0.7,
   )

   # 4. Parse JSON from the assistant
   data = response.choices[0].message["content"]
   one_liner = data["one_liner"]
   content    = data["content"]

   ```

6. **Persist Explanation**

   ```sql
   INSERT INTO explanations
     (slide_id, lecture_id, slide_number, content, one_liner, metadata)
   VALUES
     (
       (SELECT id FROM slides
         WHERE lecture_id = :lecture_id
           AND slide_number = :slide_number),
       :lecture_id, :slide_number,
       :content, :one_liner, '{}'::JSONB
     );

   ```

7. **Update Lecture Progress**

   ```sql
   UPDATE lectures
      SET processed_slides = processed_slides + 1
    WHERE id   = :lecture_id

   ```

8. **Check for Completion & Enqueue summary**

   ```sql
   SELECT processed_slides, total_slides
     FROM lectures
    WHERE id   = :lecture_id

   ```

   If `processed_slides = total_slides` , update lecture `status` to `summarising` and enqueue summary queue

   ```python
   pgmq.send("summary_queue", {"lecture_id": lecture_id})

   ```

9. **Logging & Metrics**

   Emit timing, token usage, and errors. Return HTTP 200 only after all steps complete successfully.

---

## 3.5. Summary Orchestrator (Go)

**Trigger:** new message on `summary_queue`

1. **Poll & Receive** → `{ lecture_id }`
2. **Call Python Summary Service**

   ```
   POST http://python-ai/summarize
   Body: { "lecture_id": … }

   ```

3. **Ack or Retry**
   - On HTTP 200: delete message, emit success metrics.
   - **Error Handling**: let the Go orchestrator retry with exponential backoff; on repeated failures, move the job to your DLQ, update `lectures.status = 'failed'` and set `lectures.error_message`

### **3.5.1. Python Summary Service**

1. **Gather Explanations**

   ```sql
   SELECT content FROM explanations
    WHERE lecture_id = :lectureId
    ORDER BY slide_number;
   ```

2. **Build Prompt**

   ```python
   import openai

   # 1. System prompt
   system_msg = """
   You are an expert AI teaching assistant for technical engineering lectures.
   Given a sequence of slide-by-slide explanations, produce a cohesive lecture summary that:
   1. Synthesizes the main learning objectives.
   2. Highlights how the slide-level insights connect into a unified narrative.
   3. Uses advanced engineering terminology appropriately.
   4. Is concise — no more than 300 words.
   Deliver the result as a single paragraph.
   """

   # 2. Build user content
   lecture_title = "Dynamics of Fluid Flow"
   slide_explanations = [
       "Slide 1 explanation …",
       "Slide 2 explanation …",
       # …etc…
   ]

   user_content = f"Below are per-slide explanations for the lecture "{lecture_title}":\n\n"
   for i, expl in enumerate(slide_explanations, start=1):
       user_content += f"[Slide {i}] {expl}\n\n"
   user_content += "Please follow the instructions above and return only the final summary paragraph."

   # 3. Call the Responses API
   response = openai.responses.create(
       model="gpt-4o",
       messages=[
           {"role": "system", "content": system_msg},
           {"role": "user",   "content": user_content}
       ],
       temperature=0.3,
       max_tokens=400,
   )

   # 4. Extract the summary
   summary = response.choices[0].message["content"].strip()
   print("Lecture Summary:\n", summary)

   ```

3. **Call LLM** → get summary.
4. **Insert** into `summaries(lecture_id, content…)`.
5. Update lecture `status` to `complete` and set `completed_at = NOW()`
6. **Metrics & Errors**: log token usage, cost, and fallback on over-length.
