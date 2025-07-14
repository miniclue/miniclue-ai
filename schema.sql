-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector

SET search_path TO public;

-------------------------------------------------------------------------------
-- ENUM Types
-------------------------------------------------------------------------------
-- Expanded status to track the full pipeline lifecycle
CREATE TYPE lecture_status AS ENUM (
  'uploading',
  'pending_processing',
  'parsing',
  'explaining',
  'summarising',
  'complete',
  'failed'
);

-- Status for dead-letter queue messages
CREATE TYPE dlq_message_status AS ENUM (
  'unprocessed',
  'processed',
  'ignored'
);

-------------------------------------------------------------------------------
-- 1. Course Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS courses (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  title       TEXT        NOT NULL,
  description TEXT        DEFAULT '',
  is_default  BOOLEAN     NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_default_course_per_user ON courses(user_id) WHERE is_default;
CREATE INDEX IF NOT EXISTS idx_courses_user_id ON courses(user_id);

-------------------------------------------------------------------------------
-- 2. User Profile Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_profiles (
  user_id    UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  name       TEXT        DEFAULT '',
  email      TEXT        DEFAULT '',
  avatar_url TEXT        DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-------------------------------------------------------------------------------
-- 3. Lecture Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lectures (
  id                     UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                UUID            NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  course_id              UUID            NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  title                  TEXT            NOT NULL,
  storage_path           TEXT            NOT NULL DEFAULT '',
  status                 lecture_status  NOT NULL DEFAULT 'uploading',
  error_details          JSONB,
  -- Explanation track progress
  total_slides           INT             NOT NULL DEFAULT 0,
  processed_slides       INT             NOT NULL DEFAULT 0,
  -- Search-Enrichment track progress and rendezvous flag
  total_sub_images       INT             NOT NULL DEFAULT 0,
  processed_sub_images   INT             NOT NULL DEFAULT 0,
  embeddings_complete    BOOLEAN         NOT NULL DEFAULT FALSE,
  -- Timestamps
  created_at             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
  updated_at             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
  accessed_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
  completed_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lectures_user_id   ON lectures(user_id);
CREATE INDEX IF NOT EXISTS idx_lectures_course_id ON lectures(course_id);

-------------------------------------------------------------------------------
-- 4. Slide Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS slides (
  id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  lecture_id           UUID        NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
  slide_number         INT         NOT NULL,
  raw_text             TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(lecture_id, slide_number)
);
CREATE INDEX IF NOT EXISTS idx_slides_lecture_id    ON slides(lecture_id);
CREATE INDEX IF NOT EXISTS idx_slides_lecture_slide ON slides(lecture_id, slide_number);

-------------------------------------------------------------------------------
-- 5. Chunk Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  slide_id     UUID        NOT NULL REFERENCES slides(id) ON DELETE CASCADE,
  lecture_id   UUID        NOT NULL,
  slide_number INT         NOT NULL,
  chunk_index  INT         NOT NULL,
  text         TEXT        NOT NULL,
  token_count  INT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(slide_id, chunk_index),
  FOREIGN KEY (lecture_id, slide_number) REFERENCES slides(lecture_id, slide_number) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunks_lecture_slide ON chunks(lecture_id, slide_number);

-------------------------------------------------------------------------------
-- 6. Embedding Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS embeddings (
  chunk_id     UUID         PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
  slide_id     UUID         NOT NULL,
  lecture_id   UUID         NOT NULL,
  slide_number INT          NOT NULL,
  vector       VECTOR(1536) NOT NULL,
  metadata     JSONB        NOT NULL DEFAULT '{}'::JSONB,
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE,
  FOREIGN KEY (slide_id) REFERENCES slides(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_embeddings_vector ON embeddings USING ivfflat(vector) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_embeddings_lecture_slide ON embeddings(lecture_id, slide_number);

-------------------------------------------------------------------------------
-- 7. Summary Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS summaries (
  lecture_id  UUID        PRIMARY KEY REFERENCES lectures(id) ON DELETE CASCADE,
  content     TEXT        NOT NULL,
  metadata    JSONB       NOT NULL DEFAULT '{}'::JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_summaries_by_lecture ON summaries(lecture_id);

-------------------------------------------------------------------------------
-- 8. Explanation Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS explanations (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  slide_id     UUID        NOT NULL REFERENCES slides(id) ON DELETE CASCADE,
  lecture_id   UUID        NOT NULL,
  slide_number INT         NOT NULL,
  content      TEXT        NOT NULL,
  one_liner    TEXT        NOT NULL DEFAULT '',
  slide_type   TEXT,
  metadata     JSONB       NOT NULL DEFAULT '{}'::JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(slide_id),
  FOREIGN KEY (lecture_id, slide_number) REFERENCES slides(lecture_id, slide_number) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_explanations_lecture_slide ON explanations(lecture_id, slide_number);

-------------------------------------------------------------------------------
-- 9. Slide Images Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS slide_images (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  slide_id       UUID        NOT NULL REFERENCES slides(id) ON DELETE CASCADE,
  lecture_id     UUID        NOT NULL,
  image_hash     TEXT        NOT NULL, -- Crucial for propagation
  storage_path   TEXT        NOT NULL,
  type           TEXT, -- Can be NULL until analysis is complete
  ocr_text       TEXT,
  alt_text       TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- width, height, and image_index removed for simplicity
  FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE
);
-- Composite index for efficient lookups and propagation
CREATE INDEX IF NOT EXISTS idx_slide_images_lecture_hash ON slide_images(lecture_id, image_hash);

-------------------------------------------------------------------------------
-- 10. Note Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notes (
  id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  lecture_id UUID        NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
  content    TEXT        NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notes_user_id    ON notes(user_id);
CREATE INDEX IF NOT EXISTS idx_notes_lecture_id ON notes(lecture_id);

-------------------------------------------------------------------------------
-- 11. Dead-Letter Queue Table
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dead_letter_messages (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subscription_name   TEXT NOT NULL,
  message_id          TEXT NOT NULL,
  payload             JSONB NOT NULL,
  attributes          JSONB,
  status              dlq_message_status NOT NULL DEFAULT 'unprocessed',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dead_letter_messages_status ON dead_letter_messages(status);
CREATE INDEX IF NOT EXISTS idx_dead_letter_messages_created_at ON dead_letter_messages(created_at);

-------------------------------------------------------------------------------
-- 12. Row-Level Security (RLS) Policies
-------------------------------------------------------------------------------

-- Enable RLS for all relevant tables
ALTER TABLE public.courses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lectures ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.slides ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.explanations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.slide_images ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dead_letter_messages ENABLE ROW LEVEL SECURITY;

-- 1. courses: Users can manage their own courses fully.
CREATE POLICY "Allow all access to own courses" ON public.courses
  FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- 2. user_profiles: Users can manage their own profiles fully.
CREATE POLICY "Allow all access to own profile" ON public.user_profiles
  FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- 3. lectures: Users can manage their own lectures fully.
CREATE POLICY "Allow all access to own lectures" ON public.lectures
  FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- 4. slides: Users can only read slides from lectures they own.
-- Slides are created by backend processes, not directly by users.
CREATE POLICY "Allow read access to slides of own lectures" ON public.slides
  FOR SELECT
  USING (EXISTS (SELECT 1 FROM lectures WHERE lectures.id = slides.lecture_id AND lectures.user_id = auth.uid()));

-- 5. chunks: Users can only read text chunks from lectures they own.
-- Chunks are created by backend processes.
CREATE POLICY "Allow read access to chunks of own lectures" ON public.chunks
  FOR SELECT
  USING (EXISTS (SELECT 1 FROM lectures WHERE lectures.id = chunks.lecture_id AND lectures.user_id = auth.uid()));

-- 6. embeddings: Users can only read embeddings from lectures they own.
-- Embeddings are created by backend processes for future RAG/chat features.
CREATE POLICY "Allow read access to embeddings of own lectures" ON public.embeddings
  FOR SELECT
  USING (EXISTS (SELECT 1 FROM lectures WHERE lectures.id = embeddings.lecture_id AND lectures.user_id = auth.uid()));

-- 7. summaries: Users can only read summaries for lectures they own.
-- Summaries are generated by backend AI processes.
CREATE POLICY "Allow read access to summaries of own lectures" ON public.summaries
  FOR SELECT
  USING (EXISTS (SELECT 1 FROM lectures WHERE lectures.id = summaries.lecture_id AND lectures.user_id = auth.uid()));

-- 8. explanations: Users can only read explanations for lectures they own.
-- Explanations are generated by backend AI processes.
CREATE POLICY "Allow read access to explanations of own lectures" ON public.explanations
  FOR SELECT
  USING (EXISTS (SELECT 1 FROM lectures WHERE lectures.id = explanations.lecture_id AND lectures.user_id = auth.uid()));

-- 9. slide_images: Users can only read slide image records for lectures they own.
-- Slide images are created and processed by backend services.
CREATE POLICY "Allow read access to slide_images of own lectures" ON public.slide_images
  FOR SELECT
  USING (EXISTS (SELECT 1 FROM lectures WHERE lectures.id = slide_images.lecture_id AND lectures.user_id = auth.uid()));

-- 10. notes: Users can manage their own notes.
-- The check ensures notes can only be created for lectures the user owns.
CREATE POLICY "Allow all access to own notes" ON public.notes
  FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (
    auth.uid() = user_id AND
    EXISTS (SELECT 1 FROM lectures WHERE lectures.id = notes.lecture_id AND lectures.user_id = auth.uid())
  );

-- 11. dead_letter_messages: No access for regular users.
-- These are internal records for backend debugging, accessible only via service_role.
CREATE POLICY "Deny all access to dead_letter_messages" ON public.dead_letter_messages
  FOR ALL
  USING (false)
  WITH CHECK (false);
