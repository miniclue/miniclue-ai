from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_name: str = ""
    s3_endpoint_url: str = ""
    postgres_dsn: str = ""
    ingestion_queue: str = ""
    embedding_queue: str = ""
    explanation_queue: str = ""
    summary_queue: str = ""
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: str = ""
    openai_api_base_url: str = ""
    xai_api_key: str = ""
    xai_api_base_url: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
