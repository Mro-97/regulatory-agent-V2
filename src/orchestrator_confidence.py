"""src/orchestrator_confidence.py — Confiance, scores et réponses finales.

Extraits de `src/orchestrator.py` (1096 lignes). Trois responsabilités
distinctes du pipeline, réunies parce qu'elles portent toutes sur la MÊME
question : quelle confiance accorder à une réponse, et comment la présenter.

- `confiance_apres_citation` : l'ancrage des citations corrige le niveau ;
- `doit_soumettre_validation` : décide du passage en revue humaine ;
- `construire_reponse_question` / `score_correspondance` : assemblent la
  réponse d'API et le score moyen.

Aucune de ces fonctions ne dépend de l'`Orchestrateur` : elles prennent leurs
arguments et rendent un résultat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from src.models import EvidenceRecuperee, NiveauConfiance, SortieAgent
from src.schemas import ReponseQuestion, RequeteQuestion

if TYPE_CHECKING:
    from src.agents.citation import ResultatCitation

# Seule une réponse ELEVE sort sans revue humaine ; MOYEN et en dessous sont
# escaladés. Tuple (et non liste) : ces valeurs sont comparées, jamais mutées.
CONFIANCES_A_VALIDER = (
    NiveauConfiance.MOYEN,
    NiveauConfiance.FAIBLE,
    NiveauConfiance.INCERTAIN,
)

# Ordre décroissant de confiance, pour « abaisser d'un cran ».
ORDRE_CONFIANCE = (
    NiveauConfiance.ELEVE,
    NiveauConfiance.MOYEN,
    NiveauConfiance.FAIBLE,
    NiveauConfiance.INCERTAIN,
)


def confiance_apres_citation(
    niveau: NiveauConfiance, resultat: ResultatCitation | None
) -> NiveauConfiance:
    """Abaisse la confiance si la réponse est majoritairement non ancrée.

    L'agent Citation compare les passages cités aux preuves :
    `citations_douteuses` = citations non retrouvées mot pour mot (souvent
    des paraphrases légitimes). Règles :
    - aucune citation vérifiée mais au moins une douteuse → INCERTAIN ;
    - plus de douteuses que de vérifiées → un cran plus bas ;
    - sinon (quelques paraphrases parmi des citations ancrées) → inchangé.
    `resultat` absent (étape ignorée / échec) → inchangé.
    """
    if resultat is None or not resultat.citations_douteuses:
        return niveau
    if not resultat.citations_verifiees:
        return NiveauConfiance.INCERTAIN
    if len(resultat.citations_douteuses) <= len(resultat.citations_verifiees):
        return niveau
    idx = min(ORDRE_CONFIANCE.index(niveau) + 1, len(ORDRE_CONFIANCE) - 1)
    return ORDRE_CONFIANCE[idx]


def doit_soumettre_validation(
    requete: RequeteQuestion, niveau_confiance: NiveauConfiance
) -> bool:
    """True si l'utilisateur l'a demandé ou si la confiance n'est pas `ELEVE`.

    Seule une réponse `ELEVE` (preuves fortement pertinentes, pas de refus
    LLM) sort sans revue humaine ; `MOYEN` et en dessous sont escaladés.
    """
    return (
        requete.demander_validation_humaine or niveau_confiance in CONFIANCES_A_VALIDER
    )


def score_correspondance(evidences: list[EvidenceRecuperee]) -> float | None:
    """Similarité cosinus moyenne des preuves (None si aucun score)."""
    scores = [e.score_similarite for e in evidences if e.score_similarite is not None]
    return round(sum(scores) / len(scores), 4) if scores else None


def reponse_retrieval_indisponible(request_id: UUID) -> ReponseQuestion:
    """Réponse-fallback lorsque le Retriever a échoué complètement."""
    return ReponseQuestion(
        request_id=request_id,
        reponse="Le service de recherche est temporairement indisponible.",
        niveau_confiance=NiveauConfiance.INCERTAIN,
    )


def mode_depuis_agents(agents: list[SortieAgent]) -> str:
    """Mode de rédaction réel, lu sur la SortieAgent de l'Explainer.

    Rend visible une dégradation jusqu'ici silencieuse : quand la synthèse LLM
    échoue, l'Explainer bascule sur un assemblage brut (cf.
    `_synthetiser_avec_llm`) et le client reçoit le même format de réponse,
    sans aucun moyen de le savoir. « assemblage » signale donc que la réponse
    n'a PAS été rédigée — c'est un vidage de passages.
    """
    for agent in agents:
        if agent.nom_agent == "Explainer":
            mode = agent.contenu.get("mode")
            if isinstance(mode, str) and mode:
                return mode
    return "llm"


def construire_reponse_question(
    request_id: UUID,
    reponse_texte: str,
    evidences: list[EvidenceRecuperee],
    niveau_confiance: NiveauConfiance,
    soumettre_validation: bool,
    tache_validation_id: UUID | None,
    mode_reponse: str = "llm",
) -> ReponseQuestion:
    """Assemble le ReponseQuestion final renvoyé à l'API.

    `soumettre_validation` doit être la soumission EFFECTIVE (H1) : elle vaut
    False si la tâche Redis n'a pas pu être créée, pour que
    `en_attente_validation` ne mente jamais à l'appelant.
    """
    return ReponseQuestion(
        request_id=request_id,
        reponse=reponse_texte,
        evidences=evidences,
        niveau_confiance=niveau_confiance,
        score_correspondance=score_correspondance(evidences),
        en_attente_validation=soumettre_validation,
        tache_validation_id=tache_validation_id,
        mode_reponse=mode_reponse,
    )
