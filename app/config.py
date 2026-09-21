from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://measure:measurepass@localhost:5432/measurements"
    pose_model_path: str = "models/pose_landmarker_full.task"
    store_images: bool = True
    image_dir: str = "data/uploads"
    max_upload_bytes: int = 15_000_000
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
