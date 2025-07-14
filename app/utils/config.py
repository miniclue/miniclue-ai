from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_name: str = ""
    s3_endpoint_url: str = ""
    postgres_dsn: str = ""
    # Pub/Sub Topics
    gcp_project_id: str = ""
    ingestion_topic: str = ""
    image_analysis_topic: str = ""
    embedding_topic: str = ""
    explanation_topic: str = ""
    summary_topic: str = ""
    # OpenAI
    embedding_model: str = "text-embedding-3-small"
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
    # Mock LLM calls
    mock_llm_calls: bool = False
    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    # Pub/Sub Emulator
    pubsub_emulator_host: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
