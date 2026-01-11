from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Local & Github Secrets ---

    # App Environment
    app_env: str = "prod"

    # S3
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_name: str = "miniclue-documents-local"
    s3_endpoint_url: str = "http://127.0.0.1:54321/storage/v1/s3"

    # Postgres
    postgres_dsn: str = ""

    # GCP
    gcp_project_id: str = ""
    ingestion_topic: str = "ingestion"
    image_analysis_topic: str = "image-analysis"
    embedding_topic: str = "embedding"

    # --- Local Secrets ---

    # Server Config
    host: str = "127.0.0.1"
    port: int = 8000

    # Pub/Sub Emulator
    pubsub_emulator_host: str = "localhost:8085"

    # --- GitHub Secrets ---

    # GCP
    cloud_run_service_account_email: str = ""
    gcp_artifact_registry_repo: str = ""
    gcp_region: str = ""
    gcp_service_account: str = ""
    gcp_workload_identity_provider: str = ""

    # Pub/Sub
    pubsub_base_url: str = ""
    pubsub_service_account_email: str = ""

    # PostHog
    posthog_api_key: str = ""
    posthog_api_url: str = "https://us.i.posthog.com"

    # --- Internal Config ---

    # OpenAI
    openai_api_base_url: str = "https://api.openai.com/v1"
    # Gemini
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    # Anthropic
    anthropic_api_base_url: str = "https://api.anthropic.com/v1"
    # Grok
    xai_api_base_url: str = "https://api.x.ai/v1"
    # DeepSeek
    deepseek_api_base_url: str = "https://api.deepseek.com"

    # Models
    embedding_model: str = "gemini-embedding-001"
    image_analysis_model: str = "gemini-2.5-flash-lite"
    query_rewriter_model: str = "gemini-2.5-flash-lite"
    title_model: str = "gemini-2.5-flash-lite"

    # RAG
    rag_top_k: int = 5

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
