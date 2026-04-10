from fastapi import APIRouter, HTTPException
from typing import List, Dict
from app.models.openai_config import OpenAIModel, AddModelRequest
from app.core.config import settings
import json

router = APIRouter()

def load_models() -> Dict[str, OpenAIModel]:
    """Load models from config file"""
    if not settings.MODELS_CONFIG_FILE.exists():
        return {}
    
    try:
        with open(settings.MODELS_CONFIG_FILE, 'r') as f:
            data = json.load(f)
            models = {}
            for name, model_data in data.items():
                # Skip comment entries (those starting with _)
                if name.startswith('_'):
                    continue
                # Skip if model_data is not a dict
                if not isinstance(model_data, dict):
                    continue
                try:
                    # Handle backward compatibility: if api_model is missing, use name
                    if 'api_model' not in model_data:
                        model_data['api_model'] = model_data.get('name', name)
                    models[name] = OpenAIModel(**model_data)
                except Exception as e:
                    print(f"Error loading model '{name}': {e}")
            return models
    except Exception as e:
        print(f"Error loading models file: {e}")
        return {}

def save_models(models: Dict[str, OpenAIModel]):
    """Save models to config file"""
    try:
        with open(settings.MODELS_CONFIG_FILE, 'w') as f:
            data = {
                name: model.model_dump()
                for name, model in models.items()
            }
            json.dump(data, f, indent=4)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving models: {str(e)}")

@router.get("/models", response_model=Dict[str, OpenAIModel])
async def get_models():
    """Get all configured OpenAI models"""
    return load_models()

@router.post("/models")
async def add_model(request: AddModelRequest):
    """Add or update an OpenAI model configuration"""
    try:
        models = load_models()
        
        new_model = OpenAIModel(
            name=request.model_name,
            api_model=request.api_model,
            voices=request.voices,
            base_url=request.base_url,
            api_key=request.api_key
        )
        
        models[request.model_name] = new_model
        save_models(models)
        
        return {
            "message": "Model added successfully",
            "model": new_model
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/models/{model_name}")
async def delete_model(model_name: str):
    """Delete a model configuration"""
    try:
        models = load_models()
        
        if model_name not in models:
            raise HTTPException(status_code=404, detail="Model not found")
        
        del models[model_name]
        save_models(models)
        
        return {"message": "Model deleted successfully", "model_name": model_name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/models/{model_name}/voices", response_model=List[str])
async def get_model_voices(model_name: str):
    """Get available voices for a model"""
    models = load_models()
    
    if model_name not in models:
        raise HTTPException(status_code=404, detail="Model not found")
    
    return models[model_name].voices
