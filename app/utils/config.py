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
    openai_api_base_url: str = ""
    # Groq
    xai_api_key: str = ""
    xai_api_base_url: str = ""
    mock_llm_calls: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
