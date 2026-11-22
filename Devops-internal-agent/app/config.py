"""Application configuration with security-compliant defaults."""

from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    poc_mode: str = "mock"  # "mock" | "live"
    app_host: str = "0.0.0.0"
    app_port: int = 8005

    api_keys: str = "dev-retool-poc-key-change-me"
    require_auth: bool = True

    data_dir: str = "./data"
    skills_dir: str = "./skills"
    audit_log_path: str = "./data/audit_logs.jsonl"
    vector_db_path: str = "./data/rag_store.db"

    llm_provider: str = "local_fallback"  # "local_fallback" | "ollama" | "openai_compatible"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_embed_model: str = "nomic-embed-text"

    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    enable_secret_redaction: bool = True
    read_only_db_enforced: bool = True
    allow_ci_mutations: bool = False
    max_rag_results: int = 4

    github_token: str = ""
    github_default_org: str = "my-company"
    github_default_repo: str = "platform-infra"

    aws_region: str = "us-east-1"

    postgres_dsn: str = ""
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "devops_ro"
    mysql_password: str = ""
    mysql_database: str = "orders_ro"

    @property
    def valid_api_keys(self) -> List[str]:
        """Return normalized list of configured API keys."""
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    def ensure_directories(self) -> None:
        """Create required local storage directories if missing."""
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.audit_log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.vector_db_path).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
