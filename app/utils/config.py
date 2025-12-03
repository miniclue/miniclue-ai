from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App Environment
    app_env: str = "prod"
    # S3
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_name: str = ""
    s3_endpoint_url: str = ""
    # Postgres
    postgres_dsn: str = ""
    # GCP
    gcp_project_id: str = ""
    ingestion_topic: str = ""
    image_analysis_topic: str | None = None
    explanation_topic: str | None = None
    summary_topic: str | None = None
    embedding_topic: str | None = None
    # Pub/Sub
    pubsub_base_url: str = ""
    pubsub_service_account_email: str = ""
    # OpenAI
    openai_api_base_url: str = "https://api.openai.com/v1"
    # Gemini
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # Anthropic
    anthropic_api_base_url: str = "https://api.anthropic.com/v1"
    # Grok
    xai_api_base_url: str = "https://api.x.ai/v1"
    # DeepSeek
    deepseek_api_base_url: str = "https://api.deepseek.com"
    # PostHog Configuration
    posthog_api_key: str = ""
    posthog_api_url: str = "https://us.i.posthog.com"
    # Models
    embedding_model: str = "text-embedding-3-small"
    image_analysis_model: str = "gpt-4.1-nano"
    explanation_model: str = "gpt-4o-mini"
    summary_model: str = "gpt-4.1-nano"
    # RAG
    rag_top_k: int = 5
    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    # Pub/Sub Emulator
    pubsub_emulator_host: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
