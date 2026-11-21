from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Cloud Disk"
    data_dir: Path = Path("./data")
    database_url: str | None = None
    disk_root: Path = Path("./disk")
    disk_label: str = "Drive"
    host: str = "0.0.0.0"
    port: int = 8098
    app_tz: str = "America/New_York"
    access_code: str = ""
    # Warn when the mounted folder is smaller than a portable drive.
    expect_min_bytes: int = 200 * 1024**3

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir / 'cloud-disk.db'}"


settings = Settings()
