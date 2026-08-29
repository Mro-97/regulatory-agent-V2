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
import os
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
            mode: "real" ou "mock". Si None, lit ORCHESTRATEUR_MODE
                  dans l'environnement, défaut "real".
        """
        self.mode = mode or os.environ.get("ORCHESTRATEUR_MODE", "real")
        if self.mode not in ("real", "mock"):
            logger.warning("Mode inconnu '%s', bascule sur 'real'.", self.mode)
            self.mode = "real"

        # Agents — instanciés en lazy lors du premier appel
        self._retriever: Retriever | None = None
        self._ingester: Ingester | None = None
        # M5 : le client HTTP inter-machines (Mac B / Mac C) est supprimé
        # avec l'architecture unique. Voir aussi `_http()`, retiré.

        # Sérialise l'usage du registre MLX (_CacheGeneration) : un seul
        # modèle de génération est actif à la fois (swap/unload sur bascule
        # d'agent). Sans ce verrou, deux requêtes /ask concurrentes
        # déportées en thread (voir _executer_bloquant) pourraient faire
        # basculer/décharger le modèle en pleine génération de l'autre.
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

    async def _executer_bloquant(  # noqa: D417
        self,
        fonction: Callable[..., T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Exécute un appel synchrone potentiellement long (chargement/inférence
        MLX) dans un thread séparé, pour ne jamais geler la boucle asyncio
        (sinon /health, /pending et le Watcher deviennent indisponibles
        pendant toute la durée du retrieval + de la génération LLM).

        Protégé par _verrou_agents : le registre MLX (_CacheGeneration)
        ne garde qu'un seul modèle actif à la fois et le décharge lors
        d'un changement d'agent — deux appels concurrents non sérialisés
        pourraient faire basculer le modèle pendant qu'une autre requête
        génère encore avec lui.

        Args:
            fonction: Callable synchrone à exécuter (méthode d'agent).
            *args, **kwargs: Transmis tels quels à `fonction`.

        Returns:
            La valeur de retour de `fonction`.
        """  # noqa: D205
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
        """Traite une question réglementaire via le pipeline multi-agent.

        Mode real  : Retriever Qdrant → Temporal (filtre déterministe)
                     → Explainer (assemblage brut) → audit
        Mode mock  : retourne une réponse simulée immédiatement.

        Args:
            requete: Question et paramètres de l'utilisateur.

        Returns:
            ReponseQuestion avec réponse, preuves et niveau de confiance.
        """
        request_id = uuid4()
        agents_executes: list[SortieAgent] = []
        evidences: list[EvidenceRecuperee] = []

        type_pipeline = _classifier_requete(requete.question, requete.date_contexte)
        logger.info(
            "Traitement request_id=%s mode=%s type=%s question=%r",
            request_id,
            self.mode,
            type_pipeline,
            requete.question[:80],
        )

        # ----------------------------------------------------------
        # Mode mock
        # ----------------------------------------------------------
        if self.mode == "mock":
            reponse = (
                f"[MODE MOCK] Question reçue : '{requete.question}' "
                f"| type : {type_pipeline} | date : {requete.date_contexte}"
            )
            audit = EnregistrementAudit(
                request_id=request_id,
                user_query=requete.question,
                date_contexte=requete.date_contexte,
                reponse_finale=reponse,
                niveau_confiance=NiveauConfiance.INCERTAIN,
            )
            audit.hash_courant = audit.calculer_hash()
            await self._persister_audit(audit)
            return ReponseQuestion(
                request_id=request_id,
                reponse=reponse,
                niveau_confiance=NiveauConfiance.INCERTAIN,
            )

        # ----------------------------------------------------------
        # Mode real — Étape 1 : Retrieval
        # ----------------------------------------------------------
        try:
            evidences, sortie_retriever = await self._etape_retrieval(
                question=requete.question,
                date_contexte=requete.date_contexte,
                filtres_themes=requete.filtres_themes,
                filtres_sources=requete.filtres_sources,
            )
            agents_executes.append(sortie_retriever)
        except Exception:
            logger.exception("Retrieval échoué")
            return ReponseQuestion(
                request_id=request_id,
                reponse="Le service de recherche est temporairement indisponible.",
                niveau_confiance=NiveauConfiance.INCERTAIN,
            )

        # ----------------------------------------------------------
        # Mode real — Étape 2 : Filtrage temporel (si applicable)
        # ----------------------------------------------------------
        if type_pipeline == "temporelle" and evidences:
            try:
                evidences, sortie_temporal = await self._etape_temporal(
                    question=requete.question,
                    date_contexte=requete.date_contexte,
                    evidences=evidences,
                )
                agents_executes.append(sortie_temporal)
            except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
                logger.warning("Agent Temporal échoué, ignoré : %s", exc)

        # ----------------------------------------------------------
        # Mode real — Étape 2b : Détection de conflit (si applicable)
        # ----------------------------------------------------------
        if type_pipeline == "conflit" and len(evidences) >= 2:
            from src.orchestrator_pipeline import etape_conflit

            sortie_conflit = await etape_conflit(
                self,
                question=requete.question,
                date_contexte=requete.date_contexte,
                evidences=evidences,
                request_id=request_id,
            )
            if sortie_conflit is not None:
                agents_executes.append(sortie_conflit)

        # ----------------------------------------------------------
        # Mode real — Étape 3 : Explication
        # ----------------------------------------------------------
        try:
            (
                reponse_texte,
                niveau_confiance,
                sortie_explainer,
            ) = await self._etape_explainer(
                question=requete.question,
                evidences=evidences,
                type_pipeline=type_pipeline,
                date_ref=requete.date_contexte,
            )
            agents_executes.append(sortie_explainer)
        except Exception:
            logger.exception("Explainer échoué")
            reponse_texte = "Erreur lors de la génération de la réponse."
            niveau_confiance = NiveauConfiance.INCERTAIN

        # ----------------------------------------------------------
        # Mode real — Étape 4 : Citations
        # ----------------------------------------------------------
        from src.orchestrator_pipeline import etape_citation

        sortie_citation = await etape_citation(self, evidences=evidences)
        if sortie_citation is not None:
            agents_executes.append(sortie_citation)

        # ----------------------------------------------------------
        # Validation humaine
        # ----------------------------------------------------------
        soumettre_validation = (
            requete.demander_validation_humaine
            or niveau_confiance in (NiveauConfiance.FAIBLE, NiveauConfiance.INCERTAIN)
        )

        tache_validation_id: UUID | None = None
        if soumettre_validation:
            tache = TacheValidation(
                type_file=TypeFilePendante.REPONSES,
                request_id=request_id,
                contenu={
                    "question": requete.question,
                    "reponse": reponse_texte,
                    "niveau_confiance": niveau_confiance.value,
                },
            )
            tache_validation_id = tache.tache_id
            await self._enregistrer_tache_redis(tache)

        # ----------------------------------------------------------
        # Audit
        # ----------------------------------------------------------
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
        await self._persister_audit(audit)

        return ReponseQuestion(
            request_id=request_id,
            reponse=reponse_texte,
            evidences=evidences,
            niveau_confiance=niveau_confiance,
            en_attente_validation=soumettre_validation,
            tache_validation_id=tache_validation_id,
        )

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingerer(self, requete: RequeteIngestion) -> ReponseIngestion:
        """Ingère un document réglementaire dans Qdrant (chunking + embedding +
        upsert), en réutilisant la logique de scripts/ingest.py.

        requete.contenu_json est requis et validé comme DocumentReglementaire —
        l'ingestion depuis une URL (requete.url) n'est pas implémentée et lève
        une ValueError explicite plutôt qu'un faux succès.

        Si le document (document_id) existe déjà dans la collection Qdrant,
        l'appel échoue avec DocumentDejaIndexeError sauf si
        requete.forcer_reindexation=True, auquel cas les chunks existants
        sont supprimés puis remplacés.

        Args:
            requete: Requête d'ingestion (source, contenu_json, forcer_reindexation).

        Returns:
            ReponseIngestion avec le nombre réel de chunks indexés.

        Raises:
            ValueError: contenu_json absent ou invalide vis-à-vis du schéma
                DocumentReglementaire.
            DocumentDejaIndexeError: document déjà indexé sans forcer_reindexation.
        """  # noqa: D205
        logger.info(
            "Ingestion déclenchée : source=%s forcer_reindexation=%s",
            requete.source,
            requete.forcer_reindexation,
        )

        if self.mode == "mock":
            document_id = (requete.contenu_json or {}).get("id", "mock")
            return ReponseIngestion(
                document_id=str(document_id),
                chunks_indexes=0,
                hash_document="",
                nouvelle_version=False,
            )

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
        """Persiste l'enregistrement d'audit via src/audit.py.
        JSONL local + PostgreSQL en 127.0.0.1 (architecture unique m4pro2).
        """  # noqa: D205
        try:
            from src.audit import obtenir_gestionnaire

            gestionnaire = await obtenir_gestionnaire()
            hash_courant = await gestionnaire.persister(audit)
            logger.info(
                "AUDIT request_id=%s hash=%s agents=%s confiance=%s",
                audit.request_id,
                hash_courant[:16],
                [a.nom_agent for a in audit.agents_executes],
                audit.niveau_confiance.value,
            )
        except Exception:
            logger.exception("Audit échoué (non bloquant)")
            logger.info(
                "AUDIT (log only) request_id=%s agents=%s confiance=%s",
                audit.request_id,
                [a.nom_agent for a in audit.agents_executes],
                audit.niveau_confiance.value,
            )
