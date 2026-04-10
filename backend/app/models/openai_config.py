from pydantic import BaseModel, ConfigDict
from typing import Dict, List, Optional

class VoiceMapping(BaseModel):
    voices: List[str]
    
class OpenAIModel(BaseModel):
    name: str  # Display name
    api_model: str  # Actual model name to use in API calls (e.g., "tts-1")
    voices: List[str]
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    
class OpenAIConfig(BaseModel):
    models: Dict[str, OpenAIModel]
    
class AddModelRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    
    model_name: str  # Display name (unique identifier)
    api_model: str  # API model name (e.g., "tts-1")
    voices: List[str]
    base_url: Optional[str] = None
    api_key: Optional[str] = None
