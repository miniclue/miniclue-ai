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
| **AI Microservices**                       | Python (FastAPI)                                |
| **PDF Parsing**                            | PyMuPDF                                         |
| **Embeddings**                             | OpenAI / Groq                                   |
| **LLM Inference**                          | OpenAI / Groq                                   |
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
   - Store storage path in database
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

**Input**

- `lecture_id` (UUID): The unique identifier for the lecture.
- `storage_path` (string): The path to the PDF file in object storage.

This service is triggered by a message on the `ingestion_queue`.

1.  **Initialization**: The service initializes clients for Supabase Storage (S3) and Postgres, and optionally loads the Salesforce BLIP model for image captioning if its dependencies are installed.

2.  **Download & Parse PDF**: It downloads the PDF from storage and opens it in memory using PyMuPDF. It then updates the lecture record in the database with the total number of slides.

3.  **Process Each Slide**: The service iterates through each slide of the PDF within a database transaction to ensure atomicity.

    - **Text Processing**: It extracts all raw text from the slide. This text is then broken down into smaller, overlapping chunks using the `tiktoken` library. Each chunk is saved to the database, and a corresponding job is sent to the `embedding_queue` to be processed later.
    - **Image Processing**: It extracts all embedded images from the slide. For each image, it performs several steps:
      - **Analysis**: It runs Optical Character Recognition (OCR) using Tesseract and, if enabled, generates a descriptive caption (alt-text) using the BLIP model.
      - **Classification**: Based on keywords in the caption and the amount of text from OCR, it classifies the image as either "content" (e.g., diagrams, charts) or "decorative" (e.g., logos, backgrounds).
      - **Deduplication & Storage**: It computes a perceptual hash of each image to avoid storing duplicates. Decorative images are checked against a global table and stored in a shared `global/` folder if new. Content images are checked against a lecture-specific registry and stored in a folder for that lecture.
    - **Full Slide Rendering**: Finally, it renders a high-resolution image of the entire slide. This rendered image is also processed with OCR and BLIP, and the result is saved to storage. All image metadata (paths, hashes, OCR/alt-text) is stored in the database.

4.  **Completion**: Once all slides are processed, the service logs the completion of the ingestion task.

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

**Input**

- `chunk_id` (UUID): The unique identifier for the text chunk.
- `slide_id` (UUID): The unique identifier for the slide.
- `lecture_id` (UUID): The unique identifier for the lecture.
- `slide_number` (integer): The number of the slide within the lecture.

This service is triggered by a message on the `embedding_queue` for each text chunk created during ingestion.

1.  **Fetch & Embed**: It fetches the text of a specific chunk from the database. It then calls an embedding API (like OpenAI's) to convert the text into a numerical vector.

2.  **Store Embedding**: The generated vector is saved into the `embeddings` table in the database, linked to its corresponding chunk and slide.

3.  **Update Progress & Enqueue Next Job**: The service atomically increments a counter (`processed_chunks`) for the parent slide.
    - **Explanation Job**: Once all chunks for a slide have been embedded (i.e., `processed_chunks` equals `total_chunks`), it enqueues a new job on the `explanation_queue` for that slide.
    - **Lecture Status**: It also checks if all slides for the lecture are fully embedded. If so, it updates the main lecture's status to `explaining`.

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

**Input**

- `slide_id` (UUID): The unique identifier for the slide.
- `lecture_id` (UUID): The unique identifier for the lecture.
- `slide_number` (integer): The number of the slide within the lecture.

This service is triggered by a message on the `explanation_queue` after all text chunks for a single slide have been successfully embedded. Its goal is to generate a detailed, context-aware explanation for that slide using a Retrieval-Augmented Generation (RAG) approach.

1.  **Gather Context**:

    - **Recent History**: It fetches the short, one-liner summaries from the last 1-3 slides to understand the immediate context.
    - **Current Slide Data**: It retrieves the full text and any OCR/alt-text from images on the current slide.
    - **Related Concepts (RAG)**: It creates an embedding from the current slide's full text and uses it to perform a vector similarity search across the entire lecture. This retrieves the most relevant text chunks from other slides, providing broad, lecture-wide context.

2.  **Prompt Assembly & LLM Call**: All the gathered information—recent history, current slide data, and related concepts—is assembled into a detailed prompt. It instructs the LLM to act as an AI professor and generate a clear, in-depth explanation. The LLM is asked to classify the slide's purpose (e.g., "cover", "header", "content") and return the output as a structured JSON object containing a `one_liner` summary and the full `content` in Markdown.

3.  **Persist Explanation & Update Progress**: The generated explanation and one-liner are saved to the `explanations` table. The service then atomically increments the `processed_slides` counter for the lecture.

4.  **Enqueue Summary Job**: If all slides for the lecture have been explained (`processed_slides` equals `total_slides`), it updates the lecture's status to `summarising` and enqueues a final job on the `summary_queue`.

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

**Input**

- `lecture_id` (UUID): The unique identifier for the lecture.

This service is triggered by a message on the `summary_queue` once all slides in a lecture have been explained.

1.  **Gather All Explanations**: It retrieves all the detailed, slide-by-slide explanations from the database for the entire lecture.

2.  **Build Prompt & Call LLM**: It combines all the explanations into a single, comprehensive prompt. It instructs the LLM to act as an AI professor and synthesize the information into a student-friendly "cheatsheet." The cheatsheet should start with a list of key takeaways and then provide a well-structured summary of the lecture's main topics.

3.  **Persist Summary & Finalize Lecture**: The generated Markdown summary is saved to the `summaries` table. The service then updates the main lecture's status to `complete` and records a `completed_at` timestamp, marking the successful end of the entire processing pipeline.

4.  **Metrics & Errors**: log token usage, cost, and fallback on over-length.
