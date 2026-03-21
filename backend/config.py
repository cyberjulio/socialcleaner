import os
import secrets
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    secret_key: str = secrets.token_urlsafe(32)
    host: str = "127.0.0.1"
    port: int = 8647
    db_path: str = "cleaner.db"
    browser_data_dir: str = "browser_data"

    class Config:
        env_prefix = "CLEANER_"
        env_file = ".env"


settings = Settings()
