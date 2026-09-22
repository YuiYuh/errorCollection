from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "wrongbook"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("data")
    upload_dir: Path = Path("uploads")
    backup_dir: Path = Path("backups")
    export_dir: Path = Path("exports")
    log_dir: Path = Path("logs")
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"
    ai_timeout: float = 180
    ai_max_retries: int = 1
    # Limit simultaneous vision requests so relay services are not flooded.
    ai_concurrency: int = 2
    ai_retry_base_delay: float = 2
    ai_retry_max_delay: float = 30
    ai_save_raw_response: bool = False
    ai_image_max_bytes: int = 12_000_000
    ai_image_compress: bool = True
    ai_image_max_edge: int = 1600
    ai_image_jpeg_quality: int = 85
    thumbnail_max_edge: int = 480
    ai_confidence_threshold: float = 0.85
    ai_proxy: str = "auto"
    auto_backup_hours: int = 24
    auto_backup_keep: int = 7
    tailscale_hostname: str = ""
    tailscale_serve: bool = True
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "wrongbook.db"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.backup_dir, self.export_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
