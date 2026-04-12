from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from pathlib import Path
from typing import Optional

class Settings(BaseSettings):
    """Application settings"""
    
    # App settings
    APP_NAME: str = "Audiobook Server"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent.parent.parent
    STORAGE_DIR: Path = BASE_DIR / "storage"
    EBOOKS_DIR: Path = STORAGE_DIR / "ebooks"
    AUDIOBOOKS_DIR: Path = STORAGE_DIR / "audiobooks"
    LRC_DIR: Path = STORAGE_DIR / "lrc"
    
    # OpenAI settings (optional - for self-hosted servers)
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    MODELS_CONFIG_FILE: Path = BASE_DIR / "models.json"
    
    # Audio settings
    DEFAULT_MODEL: str = "tts-1"
    DEFAULT_VOICE: str = "alloy"
    CHUNK_SIZE: int = 500  # Characters per audio chunk (smaller = better TTS quality)
    
    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"  # Ignore extra fields from .env
    )

    def __init__(self, **kwargs):
        # Explicitly set DEBUG to False, ignoring any environment variable
        kwargs['DEBUG'] = False
        super().__init__(**kwargs)
        # Create directories if they don't exist
        self.EBOOKS_DIR.mkdir(parents=True, exist_ok=True)
        self.AUDIOBOOKS_DIR.mkdir(parents=True, exist_ok=True)
        self.LRC_DIR.mkdir(parents=True, exist_ok=True)

settings = Settings()
