from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://sox:sox@localhost:5432/sox_sentinel"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_hours: int = 24
    environment: str = "development"
    log_level: str = "INFO"
    anthropic_api_key: str = ""

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()


def get_settings() -> Settings:
    return settings
