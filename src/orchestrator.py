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
import json
import logging
import os
import platform
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, TypeVar, cast
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

# ---------------------------------------------------------------------------
# Détection du type de requête
# ---------------------------------------------------------------------------

_MOTS_CLES_TEMPORELS = re.compile(
    r"\b("
    r"en\s+\d{4}"
    r"|avant\s+\d{4}"
    r"|après\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|applicable\s+(?:le|au|en)"
    r"|version\s+(?:de|du|en)\s+\d{4}"
    r"|historique"
    r"|à\s+(?:cette|la)\s+(?:date|époque)"
    r")\b",
    re.IGNORECASE,
)

_MOTS_CLES_CONFLIT = re.compile(
    r"\b(contradict|conflit|incompatible|contradiction|incohérence|contraire|oppose)\b",
    re.IGNORECASE,
)


def _classifier_requete(question: str, date_contexte: date | None) -> str:
    """Détermine le type de pipeline à exécuter.

    Returns:
        "temporelle", "conflit" ou "courante".
    """
    if date_contexte is not None:
        return "temporelle"
    if _MOTS_CLES_TEMPORELS.search(question):
        return "temporelle"
    if _MOTS_CLES_CONFLIT.search(question):
        return "conflit"
    return "courante"


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


class DocumentDejaIndexeError(Exception):
    """Levée par Orchestrateur.ingerer() quand un document_id est déjà présent
    dans Qdrant et que forcer_reindexation=False. Traduite en HTTP 409 par
    l'API (voir src/api.py).
    """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings


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

    async def _executer_bloquant(  # noqa: D417 — TODO §12 étape 4 : compléter docstrings
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
        """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings
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

    async def _etape_retrieval(
        self,
        question: str,
        date_contexte: date | None,
        filtres_themes: list[str],
        filtres_sources: list[SourceReglementaire],
    ) -> tuple[list[EvidenceRecuperee], SortieAgent]:
        """Étape 1 : récupération des passages pertinents via Qdrant.

        Returns:
            Tuple (evidences, sortie_agent).
        """
        debut = datetime.now(UTC)
        retriever = self._obtenir_retriever()

        # Déporté en thread (embedding MLX + requête Qdrant, bloquant) —
        # ne touche pas le registre de modèles de génération, donc pas
        # besoin de _verrou_agents ici.
        evidences = await asyncio.to_thread(
            retriever.retrieve,
            question=question,
            date_contexte=date_contexte,
            filtres_themes=filtres_themes,
            filtres_sources=filtres_sources,
        )

        sortie = SortieAgent(
            nom_agent="Retriever",
            machine=self._machine_pour_agent("Retriever"),
            contenu={
                "chunks_recuperes": len(evidences),
                "date_contexte": date_contexte.isoformat() if date_contexte else None,
                "filtres_themes": filtres_themes,
                "filtres_sources": [s.value for s in filtres_sources],
            },
            duree_ms=int((datetime.now(UTC) - debut).total_seconds() * 1000),
        )
        return evidences, sortie

    async def _etape_temporal(
        self,
        question: str,
        date_contexte: date | None,
        evidences: list[EvidenceRecuperee],
    ) -> tuple[list[EvidenceRecuperee], SortieAgent]:
        """Étape 2 : analyse temporelle via AgentTemporel.
        Filtre déterministe + détection d'anomalies.
        LLM (Qwen 2.5 7B) activable via use_llm=True quand les modèles
        sont disponibles.
        """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings
        from src.agents.temporal import AgentTemporel

        # use_llm=True : sous architecture unique m4pro2 (24 Go), le budget
        # RAM tolère l'annotation LLM du raisonnement temporel.
        agent = AgentTemporel(use_llm=True)
        resultat = await self._executer_bloquant(
            agent.analyser,
            question=question,
            evidences=evidences,
            date_contexte=date_contexte,
        )

        contenu = {
            "date_ref": resultat.date_ref.isoformat(),
            "avant_filtrage": len(evidences),
            "apres_filtrage": len(resultat.evidences_applicables),
            "chevauchements": resultat.chevauchements,
            "lacunes": resultat.lacunes,
            "niveau_confiance": resultat.niveau_confiance.value,
        }
        if resultat.explication_llm:
            contenu["explication_llm"] = resultat.explication_llm

        sortie = SortieAgent(
            nom_agent="Temporal",
            machine=self._machine_pour_agent("Temporal"),
            contenu=contenu,
        )
        return resultat.evidences_applicables, sortie

    async def _etape_explainer(
        self,
        question: str,
        evidences: list[EvidenceRecuperee],
        type_pipeline: str,
        date_ref: date | None = None,
    ) -> tuple[str, NiveauConfiance, SortieAgent]:
        """Étape 3 : synthèse via AgentExplainer.
        Assemblage structuré (use_llm=False) ou Qwen 2.5 7B (use_llm=True).
        """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings
        from src.agents.explainer import AgentExplainer

        # use_llm=False par défaut — activer quand Qwen est disponible
        agent = AgentExplainer(use_llm=True)
        resultat = await self._executer_bloquant(
            agent.expliquer,
            question=question,
            evidences=evidences,
            date_ref=date_ref,
            type_pipeline=type_pipeline,
        )

        sortie = SortieAgent(
            nom_agent="Explainer",
            machine=self._machine_pour_agent("Explainer"),
            contenu={
                "mode": resultat.mode,
                "evidences_utilisees": len(evidences),
                "sources_citees": len(resultat.sources_citees),
                "niveau_confiance": resultat.niveau_confiance.value,
            },
        )
        return resultat.reponse, resultat.niveau_confiance, sortie

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
        except Exception as exc:
            logger.exception("Retrieval échoué : %s", exc)  # noqa: TRY401 — TODO §12 étape 4 : réviser le message en même temps que le typage
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
            try:
                from src.agents.conflit import AgentConflit

                # use_llm=False par défaut — DeepSeek-R1 14B réservé
                # aux cas critiques confirmés par un opérateur
                agent_conflit = AgentConflit(use_llm=True)
                resultat_conflit = await self._executer_bloquant(
                    agent_conflit.analyser,
                    question=requete.question,
                    evidences=evidences,
                    date_ref=requete.date_contexte,
                )
                agents_executes.append(
                    SortieAgent(
                        nom_agent="Conflict",
                        machine=self._machine_pour_agent("Conflict"),
                        contenu={
                            "niveau_global": resultat_conflit.niveau_global.value,
                            "conflits_detectes": len(resultat_conflit.conflits),
                            "mode": resultat_conflit.mode,
                        },
                    )
                )
                # Soumettre à validation humaine si conflit probable/critique
                if resultat_conflit.necessite_validation_humaine:
                    tache_conflit = TacheValidation(
                        type_file=TypeFilePendante.LIENS,
                        request_id=request_id,
                        contenu={
                            "question": requete.question,
                            "conflits": [
                                {
                                    "doc_a": c.evidence_a.document_id,
                                    "art_a": c.evidence_a.article_id,
                                    "doc_b": c.evidence_b.document_id,
                                    "art_b": c.evidence_b.article_id,
                                    "niveau": c.niveau.value,
                                    "description": c.description,
                                }
                                for c in resultat_conflit.conflits
                            ],
                        },
                    )
                    await self._enregistrer_tache_redis(tache_conflit)
                    logger.warning(
                        "Conflit %s soumis à validation humaine.",
                        resultat_conflit.niveau_global.value,
                    )
            except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
                logger.warning("Agent Conflict échoué, ignoré : %s", exc)

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
        except Exception as exc:
            logger.exception("Explainer échoué : %s", exc)  # noqa: TRY401 — TODO §12 étape 4 : réviser le message en même temps que le typage
            reponse_texte = "Erreur lors de la génération de la réponse."
            niveau_confiance = NiveauConfiance.INCERTAIN

        # ----------------------------------------------------------
        # Mode real — Étape 4 : Citations
        # ----------------------------------------------------------
        try:
            from src.agents.citation import AgentCitation

            agent_citation = AgentCitation(use_llm=True)
            resultat_citation = await self._executer_bloquant(
                agent_citation.generate, evidences=evidences
            )
            agents_executes.append(
                SortieAgent(
                    nom_agent="Citation",
                    machine=self._machine_pour_agent("Citation"),
                    contenu={
                        "mode": resultat_citation.mode,
                        "verifiees": len(resultat_citation.citations_verifiees),
                        "douteuses": len(resultat_citation.citations_douteuses),
                    },
                )
            )
            if resultat_citation.avertissement:
                logger.warning("Citation : %s", resultat_citation.avertissement)
        except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
            logger.warning("Agent Citation échoué, ignoré : %s", exc)

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
        """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings
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
        """Partie synchrone (bloquante) de l'ingestion — validation, vérification
        d'existence, chunking, embedding MLX et upsert Qdrant. Exécutée hors
        de la boucle asyncio via asyncio.to_thread dans ingerer().
        """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings
        from src.models import DocumentReglementaire

        if not requete.contenu_json:
            raise ValueError(  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill
                "contenu_json requis — l'ingestion depuis une URL n'est pas "
                "implémentée. Fournir le document au format DocumentReglementaire "
                "canonique (voir scripts/pdf_to_json.py)."
            )

        try:
            doc = DocumentReglementaire(**requete.contenu_json)
        except Exception as exc:
            raise ValueError(f"contenu_json invalide : {exc}") from exc  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill

        if not doc.hash_document:
            doc.hash_document = doc.calculer_hash()

        ingester = self._obtenir_ingester()
        nb_existants = ingester.compter_chunks_existants(doc.id)

        if nb_existants > 0 and not requete.forcer_reindexation:
            raise DocumentDejaIndexeError(  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill
                f"Document '{doc.id}' déjà indexé ({nb_existants} chunks) — "
                f"renvoyer avec forcer_reindexation=true pour le remplacer."
            )

        if nb_existants > 0:
            ingester.supprimer_chunks_document(doc.id)

        nb_chunks = ingester.ingest_document(doc)

        return ReponseIngestion(
            document_id=doc.id,
            chunks_indexes=nb_chunks,
            hash_document=doc.hash_document,
            nouvelle_version=nb_existants > 0,
        )

    # ------------------------------------------------------------------
    # Human-in-the-loop
    # ------------------------------------------------------------------

    async def lister_taches_pendantes(self) -> ReponseTachesPendantes:
        """Récupère les tâches en attente depuis Redis."""
        try:
            client = await self._nouveau_client_redis()
            taches: list[TacheValidation] = []
            par_file: dict[str, int] = {}

            for file in TypeFilePendante:
                # `redis.asyncio.Redis.lrange` a la même signature générique
                # `Awaitable[list[Any]] | list[Any]` — cast au moment d'awaiter.
                cles = await cast(
                    "Awaitable[list[Any]]", client.lrange(file.value, 0, -1),
                )
                par_file[file.value] = len(cles)
                for cle in cles:
                    try:
                        taches.append(TacheValidation(**json.loads(cle)))
                    except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
                        logger.warning("Tâche non parsable : %s", exc)

            await client.aclose()
            return ReponseTachesPendantes(
                total=sum(par_file.values()),
                par_file=par_file,
                taches=taches,
            )
        except Exception as exc:
            logger.exception("Redis inaccessible : %s", exc)  # noqa: TRY401 — TODO §12 étape 4 : réviser le message en même temps que le typage
            return ReponseTachesPendantes(total=0, par_file={}, taches=[])

    async def valider_tache(
        self,
        tache_id: UUID,
        decision: StatutValidation,
        commentaire: str | None = None,
    ) -> ReponseDecisionValidation:
        """Applique une décision humaine à une tâche Redis."""
        horodatage = datetime.now(UTC)
        try:
            client = await self._nouveau_client_redis()
            tache_trouvee = False
            for file in TypeFilePendante:
                # `redis.asyncio.Redis.lrange` a la même signature générique
                # `Awaitable[list[Any]] | list[Any]` — cast au moment d'awaiter.
                cles = await cast(
                    "Awaitable[list[Any]]", client.lrange(file.value, 0, -1),
                )
                for cle in cles:
                    try:
                        donnees = json.loads(cle)
                        if str(donnees.get("tache_id")) == str(tache_id):
                            donnees["statut"] = decision.value
                            donnees["horodatage_traitement"] = horodatage.isoformat()
                            donnees["commentaire_validateur"] = commentaire
                            await cast(
                                "Awaitable[int]", client.lrem(file.value, 1, cle),
                            )
                            await cast("Awaitable[int]", client.lpush(
                                f"traite_{file.value}",
                                json.dumps(donnees, ensure_ascii=False),
                            ))
                            tache_trouvee = True
                            break
                    except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
                        logger.warning("Erreur parsing tâche : %s", exc)
                if tache_trouvee:
                    break

            await client.aclose()
            if not tache_trouvee:
                raise ValueError(f"Tâche introuvable : {tache_id}")  # noqa: TRY003, TRY301

            return ReponseDecisionValidation(
                tache_id=tache_id,
                nouveau_statut=decision,
                horodatage_traitement=horodatage,
            )
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Validation échouée : {exc}") from exc  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill

    # ------------------------------------------------------------------
    # Méthodes internes
    # ------------------------------------------------------------------

    async def _enregistrer_tache_redis(self, tache: TacheValidation) -> None:
        """Enregistre une tâche dans la file Redis appropriée."""
        try:
            client = await self._nouveau_client_redis()
            await cast("Awaitable[int]", client.lpush(
                tache.type_file.value, tache.model_dump_json(),
            ))
            await client.aclose()
        except Exception as exc:
            logger.exception("Redis inaccessible, tâche non enregistrée : %s", exc)  # noqa: TRY401 — TODO §12 étape 4 : réviser le message en même temps que le typage

    async def _persister_audit(self, audit: EnregistrementAudit) -> None:
        """Persiste l'enregistrement d'audit via src/audit.py.
        JSONL local + PostgreSQL en 127.0.0.1 (architecture unique m4pro2).
        """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings
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
        except Exception as exc:
            logger.exception("Audit échoué (non bloquant) : %s", exc)  # noqa: TRY401 — TODO §12 étape 4 : réviser le message en même temps que le typage
            logger.info(
                "AUDIT (log only) request_id=%s agents=%s confiance=%s",
                audit.request_id,
                [a.nom_agent for a in audit.agents_executes],
                audit.niveau_confiance.value,
            )
