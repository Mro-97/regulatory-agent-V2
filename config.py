import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).parent

class Settings(BaseSettings):
    app_nom: str = "Regulatory Agent V2"
    app_version: str = "0.1.0"
    data_dir: Path = BASE_DIR / "data"
    raw_dir: Path = data_dir / "raw"
    indexed_dir: Path = data_dir / "indexed"
    pending_dir: Path = data_dir / "pending"
    model_orchestrateur: str = "mlx-community/Llama-3.2-3B-Instruct-4bit"
    model_retriever: str = "mlx-community/Mistral-7B-Instruct-v0.3-4bit"
    model_temporal: str = "mlx-community/Qwen2.5-7B-Instruct-4bit"
    model_explainer: str = "mlx-community/Qwen2.5-7B-Instruct-4bit"
    model_citation: str = "mlx-community/Mistral-7B-Instruct-v0.3-4bit"
    model_conflit: str = "mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "regulatory_chunks"
    redis_url: str = "redis://localhost:6379/0"
    escalation_delai_heures: int = 72
    logs_dir: Path = BASE_DIR / "logs"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

cfg = Settings()
for d in [cfg.data_dir, cfg.raw_dir, cfg.indexed_dir, cfg.pending_dir, cfg.logs_dir]:
    d.mkdir(parents=True, exist_ok=True)
