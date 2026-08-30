"""src/orchestrator.py — Orchestrateur de Regulatory Agent V2
===========================================================

Responsabilités :
- Recevoir les requêtes de l'API.
- Classifier la requête (courante / temporelle / conflit).
- Router vers les agents appropriés.
- Assembler la réponse avec preuves et citations.
- Soumettre à validation humaine si nécessaire.
- Enregistrer la trace d'audit.

Mécanisme de bascule :
  mode="real" → utilise les vrais agents (Retriever Qdrant, etc.)
  mode="mock" → retourne des données simulées sans dépendance externe

  Contrôlé par la variable d'environnement ORCHESTRATEUR_MODE
  ou par le paramètre du constructeur.

Pipeline par type de requête :
  courante    → Retriever → Explainer → Citation
  temporelle  → Retriever → Temporal  → Explainer → Citation
  conflit     → Retriever → Conflict  → Explainer → Citation

Agents non encore implémentés (Temporal, Explainer, Citation, Conflict)
→ leurs étapes sont marquées TODO et contournées proprement.

Dépendances : httpx, redis, pydantic >= 2.7
"""  # noqa: D205, D415

from __future__ import annotations

import asyncio
import logging
import platform
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID, uuid4

from config import cfg

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from scripts.ingest import Ingester

    from src.agents.retriever import Retriever

from src.models import (
    EnregistrementAudit,
    EvidenceRecuperee,
    NiveauConfiance,
    ReponseDecisionValidation,
    ReponseIngestion,
    ReponseQuestion,
    ReponseTachesPendantes,
    RequeteIngestion,
    RequeteQuestion,
    SortieAgent,
    SourceReglementaire,
    StatutValidation,
    TacheValidation,
    TypeFilePendante,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Classification extraite dans src/classification.py (§12 étape 6).
from src.classification import classifier_requete as _classifier_requete  # noqa: E402

# ---------------------------------------------------------------------------
# Orchestrateur
# ---------------------------------------------------------------------------

# Architecture unique m4pro2 : un seul hôte exécute tous les agents.
# `SortieAgent.machine` reste utile pour l'audit (traçabilité multi-hôte
# éventuelle en cas d'évolution) mais retourne désormais le nom réel de
# la machine d'exécution, plus une étiquette « Mac_A/B/C » figée qui
# renvoyait à l'ancienne architecture 3-machines abandonnée.
_MACHINE = platform.node() or "inconnue"
_MACHINE_INCONNUE = "inconnue"


# `DocumentDejaIndexeError` déplacé vers src/orchestrator_ingest.py
# (§12 étape 6). Ré-exporté ici pour compatibilité descendante (api.py et
# tests continuent à l'importer depuis src.orchestrator).
# fmt: off
from src.orchestrator_ingest import DocumentDejaIndexeError as DocumentDejaIndexeError  # noqa: E402, I001
# fmt: on


def _resoudre_mode(mode: str | None) -> str:
    """Retourne le mode effectif en repliant les valeurs inconnues sur 'real'."""
    effectif = mode or cfg.orchestrateur_mode
    if effectif not in ("real", "mock"):
        logger.warning("Mode inconnu '%s', bascule sur 'real'.", effectif)
        return "real"
    return effectif


def _journaliser_debut_traitement(
    request_id: UUID,
    mode: str,
    type_pipeline: str,
    requete: RequeteQuestion,
) -> None:
    """Trace le début d'un `traiter()` avec identifiants et question tronquée."""
    logger.info(
        "Traitement request_id=%s mode=%s type=%s question=%r",
        request_id,
        mode,
        type_pipeline,
        requete.question[:80],
    )


def _construire_audit_mock(
    requete: RequeteQuestion,
    request_id: UUID,
    reponse: str,
) -> EnregistrementAudit:
    """Construit un EnregistrementAudit pour le mode mock (hash SHA-256 injecté)."""
    audit = EnregistrementAudit(
        request_id=request_id,
        user_query=requete.question,
        date_contexte=requete.date_contexte,
        reponse_finale=reponse,
        niveau_confiance=NiveauConfiance.INCERTAIN,
    )
    audit.hash_courant = audit.calculer_hash()
    return audit


def _reponse_retrieval_indisponible(request_id: UUID) -> ReponseQuestion:
    """Réponse-fallback lorsque le Retriever a échoué complètement."""
    return ReponseQuestion(
        request_id=request_id,
        reponse="Le service de recherche est temporairement indisponible.",
        niveau_confiance=NiveauConfiance.INCERTAIN,
    )


def _doit_soumettre_validation(
    requete: RequeteQuestion, niveau_confiance: NiveauConfiance
) -> bool:
    """True si l'utilisateur l'a demandé ou si la confiance est faible/incertaine."""
    return requete.demander_validation_humaine or niveau_confiance in (
        NiveauConfiance.FAIBLE,
        NiveauConfiance.INCERTAIN,
    )


def _reponse_ingestion_mock(requete: RequeteIngestion) -> ReponseIngestion:
    """Réponse-fake retournée en mode mock (aucune action Qdrant)."""
    document_id = (requete.contenu_json or {}).get("id", "mock")
    return ReponseIngestion(
        document_id=str(document_id),
        chunks_indexes=0,
        hash_document="",
        nouvelle_version=False,
    )


def _journaliser_audit_succes(audit: EnregistrementAudit, hash_courant: str) -> None:
    """Trace un audit persisté avec succès (hash tronqué, agents, confiance)."""
    logger.info(
        "AUDIT request_id=%s hash=%s agents=%s confiance=%s",
        audit.request_id,
        hash_courant[:16],
        [a.nom_agent for a in audit.agents_executes],
        audit.niveau_confiance.value,
    )


def _journaliser_audit_fallback(audit: EnregistrementAudit) -> None:
    """Trace un audit qui n'a pas pu être persisté (log only, non bloquant)."""
    logger.info(
        "AUDIT (log only) request_id=%s agents=%s confiance=%s",
        audit.request_id,
        [a.nom_agent for a in audit.agents_executes],
        audit.niveau_confiance.value,
    )


def _construire_reponse_question(
    request_id: UUID,
    reponse_texte: str,
    evidences: list[EvidenceRecuperee],
    niveau_confiance: NiveauConfiance,
    soumettre_validation: bool,
    tache_validation_id: UUID | None,
) -> ReponseQuestion:
    """Assemble le ReponseQuestion final renvoyé à l'API."""
    return ReponseQuestion(
        request_id=request_id,
        reponse=reponse_texte,
        evidences=evidences,
        niveau_confiance=niveau_confiance,
        en_attente_validation=soumettre_validation,
        tache_validation_id=tache_validation_id,
    )


def _construire_audit_reel(
    requete: RequeteQuestion,
    request_id: UUID,
    evidences: list[EvidenceRecuperee],
    agents_executes: list[SortieAgent],
    reponse_texte: str,
    niveau_confiance: NiveauConfiance,
    soumettre_validation: bool,
) -> EnregistrementAudit:
    """Construit l'EnregistrementAudit final du pipeline réel (hash injecté)."""
    audit = EnregistrementAudit(
        request_id=request_id,
        user_query=requete.question,
        date_contexte=requete.date_contexte,
        documents_recuperes=list({e.document_id for e in evidences}),
        evidences=evidences,
        agents_executes=agents_executes,
        reponse_finale=reponse_texte,
        niveau_confiance=niveau_confiance,
        necessite_validation_humaine=soumettre_validation,
    )
    audit.hash_courant = audit.calculer_hash()
    return audit


class Orchestrateur:
    """Orchestrateur central de Regulatory Agent V2.

    Paramètres :
        mode : "real" (défaut) ou "mock".
               Peut aussi être contrôlé via ORCHESTRATEUR_MODE=mock dans .env.

    En mode "real" :
      - Le Retriever appelle Qdrant via src/agents/retriever.py.
      - Les agents non encore implémentés (Temporal, Explainer, Citation)
        sont contournés proprement avec un log explicite.

    En mode "mock" :
      - Tous les agents retournent des données simulées.
      - Aucune dépendance externe requise (Qdrant, Redis, modèles MLX).
      - Utile pour les tests de l'API sans infrastructure.
    """

    def __init__(self, mode: str | None = None) -> None:
        """Initialise l'orchestrateur sans charger aucun agent en mémoire.

        Args:
            mode: "real" ou "mock". Si None, lit `cfg.orchestrateur_mode`
                  (renseigné via ORCHESTRATEUR_MODE dans .env), défaut "real".
        """
        self.mode = _resoudre_mode(mode)
        self._retriever: Retriever | None = None
        self._ingester: Ingester | None = None
        # Sérialise l'usage du registre MLX : un seul modèle de génération
        # actif à la fois ; le verrou empêche deux threads d'entrer en swap
        # concurrent (cf. `_executer_bloquant`).
        self._verrou_agents = asyncio.Lock()
        logger.info("Orchestrateur initialisé — mode=%s", self.mode)

    # ------------------------------------------------------------------
    # Accès lazy aux agents
    # ------------------------------------------------------------------

    def _obtenir_retriever(self) -> Retriever:
        """Retourne le Retriever réel, créé au premier appel."""
        if self._retriever is None:
            from src.agents.retriever import Retriever

            self._retriever = Retriever()
            logger.info("Retriever réel initialisé.")
        return self._retriever

    def _obtenir_ingester(self) -> Ingester:
        """Retourne l'Ingester réel (scripts/ingest.py), créé au premier appel."""
        if self._ingester is None:
            from scripts.ingest import Ingester

            self._ingester = Ingester(collection_name=cfg.qdrant_collection)
            logger.info("Ingester réel initialisé.")
        return self._ingester

    async def _executer_bloquant(
        self,
        fonction: Callable[..., T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Exécute un appel synchrone (MLX) dans un thread borné par `_verrou_agents`.

        Le verrou sérialise l'usage du registre MLX (un seul modèle
        résident à la fois) : deux appels concurrents non sérialisés
        pourraient basculer le modèle pendant qu'une autre requête
        génère encore avec lui. Le thread évite de figer l'event loop.
        """
        async with self._verrou_agents:
            return await asyncio.to_thread(fonction, *args, **kwargs)

    async def _nouveau_client_redis(self) -> aioredis.Redis:
        """Client Redis asynchrone avec authentification depuis la config."""
        import redis.asyncio as aioredis

        return aioredis.Redis(
            host=cfg.redis_host,
            port=cfg.redis_port,
            password=cfg.redis_password or None,
            db=cfg.redis_db,
            decode_responses=True,
        )

    @staticmethod
    def _machine_pour_agent(nom_agent: str) -> str:
        """Retourne la machine d'exécution attendue pour un agent donné.

        Utilisée pour renseigner SortieAgent.machine dans l'audit trail.
        Un nom d'agent inconnu déclenche un warning et retourne "inconnue"
        plutôt que de renvoyer une machine erronée par défaut.

        Args:
            nom_agent: Nom de l'agent (conservé pour compatibilité de signature ;
                       ignoré sous l'architecture unique m4pro2).

        Returns:
            Le nom réel de la machine d'exécution (via `platform.node()`),
            ou `"inconnue"` si le hostname n'a pas pu être résolu.
        """
        del nom_agent  # Signature préservée pour ne pas casser les appelants.
        return _MACHINE

    # ------------------------------------------------------------------
    # Étapes du pipeline — mode real
    # ------------------------------------------------------------------

    # `_etape_retrieval`, `_etape_temporal`, `_etape_explainer` ont été
    # déplacées vers src/orchestrator_pipeline.py (§12 étape 6). Les
    # méthodes ci-dessous sont conservées comme wrappers minces pour ne
    # rien changer aux callers.

    async def _etape_retrieval(
        self,
        question: str,
        date_contexte: date | None,
        filtres_themes: list[str],
        filtres_sources: list[SourceReglementaire],
    ) -> tuple[list[EvidenceRecuperee], SortieAgent]:
        """Étape 1 déléguée à src.orchestrator_pipeline."""
        from src.orchestrator_pipeline import etape_retrieval

        return await etape_retrieval(
            self, question, date_contexte, filtres_themes, filtres_sources
        )

    async def _etape_temporal(
        self,
        question: str,
        date_contexte: date | None,
        evidences: list[EvidenceRecuperee],
    ) -> tuple[list[EvidenceRecuperee], SortieAgent]:
        """Étape 2 déléguée à src.orchestrator_pipeline."""
        from src.orchestrator_pipeline import etape_temporal

        return await etape_temporal(self, question, date_contexte, evidences)

    async def _etape_explainer(
        self,
        question: str,
        evidences: list[EvidenceRecuperee],
        type_pipeline: str,
        date_ref: date | None = None,
    ) -> tuple[str, NiveauConfiance, SortieAgent]:
        """Étape 3 déléguée à src.orchestrator_pipeline."""
        from src.orchestrator_pipeline import etape_explainer

        return await etape_explainer(self, question, evidences, type_pipeline, date_ref)

    # ------------------------------------------------------------------
    # Pipeline principal
    # ------------------------------------------------------------------

    async def traiter(self, requete: RequeteQuestion) -> ReponseQuestion:
        """Point d'entrée du pipeline multi-agent (route selon `self.mode`).

        Args:
            requete: Question et paramètres de l'utilisateur.

        Returns:
            ReponseQuestion avec réponse, preuves et niveau de confiance.
        """
        request_id = uuid4()
        type_pipeline = _classifier_requete(requete.question, requete.date_contexte)
        _journaliser_debut_traitement(request_id, self.mode, type_pipeline, requete)
        if self.mode == "mock":
            return await self._traiter_mock(requete, request_id, type_pipeline)
        return await self._traiter_pipeline_reel(requete, request_id, type_pipeline)

    async def _traiter_mock(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        type_pipeline: str,
    ) -> ReponseQuestion:
        """Produit une ReponseQuestion simulée + audit, sans dépendance externe."""
        reponse = (
            f"[MODE MOCK] Question reçue : '{requete.question}' "
            f"| type : {type_pipeline} | date : {requete.date_contexte}"
        )
        await self._persister_audit(
            _construire_audit_mock(requete, request_id, reponse)
        )
        return ReponseQuestion(
            request_id=request_id,
            reponse=reponse,
            niveau_confiance=NiveauConfiance.INCERTAIN,
        )

    async def _traiter_pipeline_reel(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        type_pipeline: str,
    ) -> ReponseQuestion:
        """Exécute les 4 étapes du pipeline réel puis assemble la ReponseQuestion."""
        agents_executes: list[SortieAgent] = []
        try:
            evidences = await self._executer_retrieval(requete, agents_executes)
        except Exception:
            logger.exception("Retrieval échoué")
            return _reponse_retrieval_indisponible(request_id)
        evidences = await self._executer_temporal_si_applicable(
            requete, type_pipeline, evidences, agents_executes
        )
        await self._executer_conflit_si_applicable(
            requete, type_pipeline, evidences, agents_executes, request_id
        )
        reponse_texte, niveau_confiance = await self._executer_explainer_avec_repli(
            requete, type_pipeline, evidences, agents_executes
        )
        await self._executer_citation(evidences, agents_executes)
        return await self._finaliser_reponse(
            requete,
            request_id,
            evidences,
            agents_executes,
            reponse_texte,
            niveau_confiance,
        )

    async def _executer_retrieval(
        self,
        requete: RequeteQuestion,
        agents_executes: list[SortieAgent],
    ) -> list[EvidenceRecuperee]:
        """Étape 1 : appelle le Retriever et ajoute sa SortieAgent à la trace."""
        evidences, sortie = await self._etape_retrieval(
            question=requete.question,
            date_contexte=requete.date_contexte,
            filtres_themes=requete.filtres_themes,
            filtres_sources=requete.filtres_sources,
        )
        agents_executes.append(sortie)
        return evidences

    async def _executer_temporal_si_applicable(
        self,
        requete: RequeteQuestion,
        type_pipeline: str,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
    ) -> list[EvidenceRecuperee]:
        """Étape 2 : filtre temporel (activé pour `type_pipeline == 'temporelle'`)."""
        if type_pipeline != "temporelle" or not evidences:
            return evidences
        try:
            evidences, sortie = await self._etape_temporal(
                question=requete.question,
                date_contexte=requete.date_contexte,
                evidences=evidences,
            )
            agents_executes.append(sortie)
        except Exception as exc:  # noqa: BLE001 — frontière externe : dégradation gracieuse, cf. skill §8
            logger.warning("Agent Temporal échoué, ignoré : %s", exc)
        return evidences

    async def _executer_conflit_si_applicable(
        self,
        requete: RequeteQuestion,
        type_pipeline: str,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
        request_id: UUID,
    ) -> None:
        """Étape 2b : détection de conflit (activée si `type_pipeline == 'conflit'`)."""
        if type_pipeline != "conflit" or len(evidences) < 2:
            return
        from src.orchestrator_pipeline import etape_conflit

        sortie = await etape_conflit(
            self,
            question=requete.question,
            date_contexte=requete.date_contexte,
            evidences=evidences,
            request_id=request_id,
        )
        if sortie is not None:
            agents_executes.append(sortie)

    async def _executer_explainer_avec_repli(
        self,
        requete: RequeteQuestion,
        type_pipeline: str,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
    ) -> tuple[str, NiveauConfiance]:
        """Étape 3 : synthèse Explainer ; sur échec, renvoie un message + INCERTAIN."""
        try:
            reponse, confiance, sortie = await self._etape_explainer(
                question=requete.question,
                evidences=evidences,
                type_pipeline=type_pipeline,
                date_ref=requete.date_contexte,
            )
            agents_executes.append(sortie)
        except Exception:
            logger.exception("Explainer échoué")
            return (
                "Erreur lors de la génération de la réponse.",
                NiveauConfiance.INCERTAIN,
            )
        return reponse, confiance

    async def _executer_citation(
        self,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
    ) -> None:
        """Étape 4 : génération/vérification des citations (ignorée si échec)."""
        from src.orchestrator_pipeline import etape_citation

        sortie = await etape_citation(self, evidences=evidences)
        if sortie is not None:
            agents_executes.append(sortie)

    async def _finaliser_reponse(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
        reponse_texte: str,
        niveau_confiance: NiveauConfiance,
    ) -> ReponseQuestion:
        """Soumet à validation si besoin, persiste l'audit, renvoie la réponse."""
        soumettre = _doit_soumettre_validation(requete, niveau_confiance)
        tache_validation_id = await self._soumettre_validation_si_besoin(
            requete, request_id, reponse_texte, niveau_confiance, soumettre
        )
        audit = _construire_audit_reel(
            requete,
            request_id,
            evidences,
            agents_executes,
            reponse_texte,
            niveau_confiance,
            soumettre,
        )
        await self._persister_audit(audit)
        return _construire_reponse_question(
            request_id,
            reponse_texte,
            evidences,
            niveau_confiance,
            soumettre,
            tache_validation_id,
        )

    async def _soumettre_validation_si_besoin(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        reponse_texte: str,
        niveau_confiance: NiveauConfiance,
        soumettre: bool,
    ) -> UUID | None:
        """Enregistre une TacheValidation Redis quand `soumettre` est True."""
        if not soumettre:
            return None
        tache = TacheValidation(
            type_file=TypeFilePendante.REPONSES,
            request_id=request_id,
            contenu={
                "question": requete.question,
                "reponse": reponse_texte,
                "niveau_confiance": niveau_confiance.value,
            },
        )
        await self._enregistrer_tache_redis(tache)
        return tache.tache_id

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingerer(self, requete: RequeteIngestion) -> ReponseIngestion:
        """Ingère un document réglementaire (chunking + embedding + upsert Qdrant).

        Mode mock : renvoie une ReponseIngestion factice. Mode real :
        délègue à `orchestrator_ingest.ingerer_sync` via un thread.

        Raises:
            MissingMetadataError / InvalidDocumentError : contenu_json absent
                ou invalide vis-à-vis du schéma DocumentReglementaire.
            DocumentAlreadyIndexedError : document déjà indexé sans
                `forcer_reindexation`.
        """
        logger.info(
            "Ingestion déclenchée : source=%s forcer_reindexation=%s",
            requete.source,
            requete.forcer_reindexation,
        )
        if self.mode == "mock":
            return _reponse_ingestion_mock(requete)
        return await asyncio.to_thread(self._ingerer_sync, requete)

    def _ingerer_sync(self, requete: RequeteIngestion) -> ReponseIngestion:
        """Ingestion synchrone déléguée à src.orchestrator_ingest."""
        from src.orchestrator_ingest import ingerer_sync

        return ingerer_sync(self._obtenir_ingester, requete)

    # ------------------------------------------------------------------
    # Human-in-the-loop
    # ------------------------------------------------------------------

    async def lister_taches_pendantes(self) -> ReponseTachesPendantes:
        """Récupère les tâches en attente depuis Redis (délégué)."""
        from src.orchestrator_validation import (
            lister_taches_pendantes as _lister,
        )

        return await _lister(self._nouveau_client_redis)

    async def valider_tache(
        self,
        tache_id: UUID,
        decision: StatutValidation,
        commentaire: str | None = None,
    ) -> ReponseDecisionValidation:
        """Applique une décision humaine à une tâche Redis (délégué)."""
        from src.orchestrator_validation import valider_tache as _valider

        return await _valider(
            self._nouveau_client_redis, tache_id, decision, commentaire
        )

    # ------------------------------------------------------------------
    # Méthodes internes
    # ------------------------------------------------------------------

    async def _enregistrer_tache_redis(self, tache: TacheValidation) -> None:
        """Enregistre une tâche dans la file Redis (délégué)."""
        from src.orchestrator_validation import (
            enregistrer_tache_redis as _enregistrer,
        )

        await _enregistrer(self._nouveau_client_redis, tache)

    async def _persister_audit(self, audit: EnregistrementAudit) -> None:
        """Persiste l'audit (JSONL local + PostgreSQL) ; jamais bloquant."""
        try:
            from src.audit import obtenir_gestionnaire

            gestionnaire = await obtenir_gestionnaire()
            hash_courant = await gestionnaire.persister(audit)
            _journaliser_audit_succes(audit, hash_courant)
        except Exception:
            logger.exception("Audit échoué (non bloquant)")
            _journaliser_audit_fallback(audit)
