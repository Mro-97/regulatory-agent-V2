from typing import Optional
from datetime import date

class Orchestrateur:
    def __init__(self):
        pass

    def _classifier_requete(self, question: str, date_contexte: Optional[date] = None) -> str:
        if "RGPD" in question:
            return "pipeline_retrieval"
        if "2023" in question or date_contexte:
            return "pipeline_temporelle"
        if "contradiction" in question:
            return "pipeline_conflit"
        return "pipeline_general"
