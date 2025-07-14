from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "prod"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_name: str = ""
    s3_endpoint_url: str = ""
    postgres_dsn: str = ""
    # Pub/Sub Topics
    gcp_project_id: str = ""
    ingestion_topic: str = ""
    image_analysis_topic: str | None = None
    explanation_topic: str | None = None
    summary_topic: str | None = None
    embedding_topic: str | None = None
    # OpenAI
    openai_api_key: str = ""
    openai_api_base_url: str = "https://api.openai.com/v1"
    # Groq
    xai_api_key: str = ""
    xai_api_base_url: str = "https://api.x.ai/v1"
    # Gemini
    gemini_api_key: str = ""
    gemini_api_base_url: str = (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    # Models
    embedding_model: str = "text-embedding-3-small"
    image_analysis_model: str = "gemini-2.5-flash-lite-preview-06-17"
    explanation_model: str = "gemini-2.5-flash-lite-preview-06-17"
    summary_model: str = "gemini-2.5-flash-lite-preview-06-17"
    # Mock LLM calls
    mock_llm_calls: bool = False
    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    # Pub/Sub Emulator
    pubsub_emulator_host: str = ""
    pubsub_base_url: str = ""
    pubsub_service_account_email: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
