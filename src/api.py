from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import date
from typing import Optional, List
from uuid import UUID
from src.orchestrator import Orchestrateur

app = FastAPI(title="Regulatory Agent API", version="0.1.0")
orchestrateur = Orchestrateur()

class QuestionRequest(BaseModel):
    question: str
    date_contexte: Optional[date] = None
    filtres_themes: List[str] = []
    demander_validation_humaine: bool = False

class QuestionResponse(BaseModel):
    request_id: UUID
    reponse: str
    niveau_confiance: str
    en_attente_validation: bool = False

@app.get("/")
async def root():
    return {"message": "Regulatory Agent API", "status": "operational"}

@app.post("/ask", response_model=QuestionResponse)
async def ask_question(req: QuestionRequest):
    try:
        route = orchestrateur._classifier_requete(req.question, req.date_contexte)
        return QuestionResponse(
            request_id=UUID(int=0),  # temporaire
            reponse=f"Réponse simulée pour la question : {req.question} (route: {route})",
            niveau_confiance="moyen"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
