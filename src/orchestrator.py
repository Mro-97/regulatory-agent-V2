"""
src/orchestrator.py — Orchestrateur de Regulatory Agent V2
===========================================================

Responsabilités (Mac A) :
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
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import httpx

from config import cfg
from src.models import (
    EnregistrementAudit,
    EvidenceRecuperee,
    NiveauConfiance,
    ReponsQuestion,
    ReponseDecisionValidation,
    ReponseIngestion,
    ReponseTachesPendantes,
    RequeteDecisionValidation,
    RequeteIngestion,
    RequeteQuestion,
    SortieAgent,
    SourceReglementaire,
    StatutValidation,
    TacheValidation,
    TypeFilePendante,
)

logger = logging.getLogger(__name__)

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


def _classifier_requete(question: str, date_contexte: Optional[date]) -> str:
    """
    Détermine le type de pipeline à exécuter.

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

# Mapping agent → machine d'exécution (contexte projet § 3).
# Source unique de vérité utilisée dans l'audit trail (SortieAgent.machine).
# Modifier ici si l'architecture distribuée change.
_MACHINE_PAR_AGENT: dict[str, str] = {
    "Orchestrateur": "Mac_A",
    "Retriever":     "Mac_B",
    "Temporal":      "Mac_B",
    "Explainer":     "Mac_B",
    "Citation":      "Mac_B",
    "Conflict":      "Mac_C",
}
_MACHINE_INCONNUE = "inconnue"


class Orchestrateur:
    """
    Orchestrateur central de Regulatory Agent V2.

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

    def __init__(self, mode: Optional[str] = None) -> None:
        """
        Initialise l'orchestrateur sans charger aucun agent en mémoire.

        Args:
            mode: "real" ou "mock". Si None, lit ORCHESTRATEUR_MODE
                  dans l'environnement, défaut "real".
        """
        self.mode = mode or os.environ.get("ORCHESTRATEUR_MODE", "real")
        if self.mode not in ("real", "mock"):
            logger.warning("Mode inconnu '%s', bascule sur 'real'.", self.mode)
            self.mode = "real"

        # Agents — instanciés en lazy lors du premier appel
        self._retriever = None
        self._client_http: Optional[httpx.AsyncClient] = None

        logger.info("Orchestrateur initialisé — mode=%s", self.mode)

    # ------------------------------------------------------------------
    # Accès lazy aux agents
    # ------------------------------------------------------------------

    def _obtenir_retriever(self):
        """Retourne le Retriever réel, créé au premier appel."""
        if self._retriever is None:
            from src.agents.retriever import Retriever
            self._retriever = Retriever()
            logger.info("Retriever réel initialisé.")
        return self._retriever

    async def _http(self) -> httpx.AsyncClient:
        """Client HTTP partagé pour les appels Mac B / Mac C."""
        if self._client_http is None or self._client_http.is_closed:
            self._client_http = httpx.AsyncClient(timeout=60.0)
        return self._client_http

    async def _nouveau_client_redis(self):
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
        """
        Retourne la machine d'exécution attendue pour un agent donné.

        Utilisée pour renseigner SortieAgent.machine dans l'audit trail.
        Un nom d'agent inconnu déclenche un warning et retourne "inconnue"
        plutôt que de renvoyer une machine erronée par défaut.

        Args:
            nom_agent: Nom de l'agent (ex. "Retriever", "Temporal").

        Returns:
            Étiquette de machine ("Mac_A" / "Mac_B" / "Mac_C" / "inconnue").
        """
        machine = _MACHINE_PAR_AGENT.get(nom_agent)
        if machine is None:
            logger.warning(
                "Agent '%s' absent du mapping _MACHINE_PAR_AGENT — audit imprécis.",
                nom_agent,
            )
            return _MACHINE_INCONNUE
        return machine

    # ------------------------------------------------------------------
    # Étapes du pipeline — mode real
    # ------------------------------------------------------------------

    async def _etape_retrieval(
        self,
        question: str,
        date_contexte: Optional[date],
        filtres_themes: list[str],
        filtres_sources: list[SourceReglementaire],
    ) -> tuple[list[EvidenceRecuperee], SortieAgent]:
        """
        Étape 1 : récupération des passages pertinents via Qdrant.

        Returns:
            Tuple (evidences, sortie_agent).
        """
        debut = datetime.now(timezone.utc)
        retriever = self._obtenir_retriever()

        evidences = retriever.retrieve(
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
            duree_ms=int(
                (datetime.now(timezone.utc) - debut).total_seconds() * 1000
            ),
        )
        return evidences, sortie

    async def _etape_temporal(
        self,
        question: str,
        date_contexte: Optional[date],
        evidences: list[EvidenceRecuperee],
    ) -> tuple[list[EvidenceRecuperee], SortieAgent]:
        """
        Étape 2 : analyse temporelle via AgentTemporel.
        Filtre déterministe + détection d'anomalies.
        LLM (Qwen 2.5 7B) activable via use_llm=True quand les modèles
        sont disponibles.
        """
        from src.agents.temporal import AgentTemporel

        # use_llm=False par défaut sur Mac A (16 Go) — activer sur Mac B
        agent = AgentTemporel(use_llm=True)
        resultat = agent.analyser(
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
        date_ref=None,
    ) -> tuple[str, NiveauConfiance, SortieAgent]:
        """
        Étape 3 : synthèse via AgentExplainer.
        Assemblage structuré (use_llm=False) ou Qwen 2.5 7B (use_llm=True).
        """
        from src.agents.explainer import AgentExplainer

        # use_llm=False par défaut — activer quand Qwen est disponible
        agent = AgentExplainer(use_llm=True)
        resultat = agent.expliquer(
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

    async def traiter(self, requete: RequeteQuestion) -> ReponsQuestion:
        """
        Traite une question réglementaire via le pipeline multi-agent.

        Mode real  : Retriever Qdrant → Temporal (filtre déterministe)
                     → Explainer (assemblage brut) → audit
        Mode mock  : retourne une réponse simulée immédiatement.

        Args:
            requete: Question et paramètres de l'utilisateur.

        Returns:
            ReponsQuestion avec réponse, preuves et niveau de confiance.
        """
        request_id = uuid4()
        agents_executes: list[SortieAgent] = []
        evidences: list[EvidenceRecuperee] = []

        type_pipeline = _classifier_requete(requete.question, requete.date_contexte)
        logger.info(
            "Traitement request_id=%s mode=%s type=%s question=%r",
            request_id, self.mode, type_pipeline, requete.question[:80],
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
            return ReponsQuestion(
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
            logger.error("Retrieval échoué : %s", exc)
            return ReponsQuestion(
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
            except Exception as exc:
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
                resultat_conflit = agent_conflit.analyser(
                    question=requete.question,
                    evidences=evidences,
                    date_ref=requete.date_contexte,
                )
                agents_executes.append(SortieAgent(
                    nom_agent="Conflict",
                    machine=self._machine_pour_agent("Conflict"),
                    contenu={
                        "niveau_global": resultat_conflit.niveau_global.value,
                        "conflits_detectes": len(resultat_conflit.conflits),
                        "mode": resultat_conflit.mode,
                    },
                ))
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
            except Exception as exc:
                logger.warning("Agent Conflict échoué, ignoré : %s", exc)

        # ----------------------------------------------------------
        # Mode real — Étape 3 : Explication
        # ----------------------------------------------------------
        try:
            reponse_texte, niveau_confiance, sortie_explainer = await self._etape_explainer(
                question=requete.question,
                evidences=evidences,
                type_pipeline=type_pipeline,
                date_ref=requete.date_contexte,
            )
            agents_executes.append(sortie_explainer)
        except Exception as exc:
            logger.error("Explainer échoué : %s", exc)
            reponse_texte = "Erreur lors de la génération de la réponse."
            niveau_confiance = NiveauConfiance.INCERTAIN

        # ----------------------------------------------------------
        # Mode real — Étape 4 : Citations
        # ----------------------------------------------------------
        try:
            from src.agents.citation import AgentCitation
            agent_citation = AgentCitation(use_llm=True)
            resultat_citation = agent_citation.generate(evidences=evidences)
            agents_executes.append(SortieAgent(
                nom_agent="Citation",
                machine=self._machine_pour_agent("Citation"),
                contenu={
                    "mode": resultat_citation.mode,
                    "verifiees": len(resultat_citation.citations_verifiees),
                    "douteuses": len(resultat_citation.citations_douteuses),
                },
            ))
            if resultat_citation.avertissement:
                logger.warning("Citation : %s", resultat_citation.avertissement)
        except Exception as exc:
            logger.warning("Agent Citation échoué, ignoré : %s", exc)

        # ----------------------------------------------------------
        # Validation humaine
        # ----------------------------------------------------------
        soumettre_validation = (
            requete.demander_validation_humaine
            or niveau_confiance in (NiveauConfiance.FAIBLE, NiveauConfiance.INCERTAIN)
        )

        tache_validation_id: Optional[UUID] = None
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

        return ReponsQuestion(
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
        """Délègue l'ingestion au pipeline scripts/ingest.py via sous-processus."""
        logger.info("Ingestion déclenchée : source=%s", requete.source)
        # TODO : appel au pipeline d'ingestion via Mac B
        return ReponseIngestion(
            document_id="inconnu",
            chunks_indexes=0,
            hash_document="",
            nouvelle_version=False,
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
                cles = await client.lrange(file.value, 0, -1)
                par_file[file.value] = len(cles)
                for cle in cles:
                    try:
                        taches.append(TacheValidation(**json.loads(cle)))
                    except Exception as exc:
                        logger.warning("Tâche non parsable : %s", exc)

            await client.aclose()
            return ReponseTachesPendantes(
                total=sum(par_file.values()),
                par_file=par_file,
                taches=taches,
            )
        except Exception as exc:
            logger.error("Redis inaccessible : %s", exc)
            return ReponseTachesPendantes(total=0, par_file={}, taches=[])

    async def valider_tache(
        self,
        tache_id: UUID,
        decision: StatutValidation,
        commentaire: Optional[str] = None,
    ) -> ReponseDecisionValidation:
        """Applique une décision humaine à une tâche Redis."""
        horodatage = datetime.now(timezone.utc)
        try:
            client = await self._nouveau_client_redis()
            tache_trouvee = False
            for file in TypeFilePendante:
                cles = await client.lrange(file.value, 0, -1)
                for cle in cles:
                    try:
                        donnees = json.loads(cle)
                        if str(donnees.get("tache_id")) == str(tache_id):
                            donnees["statut"] = decision.value
                            donnees["horodatage_traitement"] = horodatage.isoformat()
                            donnees["commentaire_validateur"] = commentaire
                            await client.lrem(file.value, 1, cle)
                            await client.lpush(
                                f"traite_{file.value}",
                                json.dumps(donnees, ensure_ascii=False),
                            )
                            tache_trouvee = True
                            break
                    except Exception as exc:
                        logger.warning("Erreur parsing tâche : %s", exc)
                if tache_trouvee:
                    break

            await client.aclose()
            if not tache_trouvee:
                raise ValueError(f"Tâche introuvable : {tache_id}")

            return ReponseDecisionValidation(
                tache_id=tache_id,
                nouveau_statut=decision,
                horodatage_traitement=horodatage,
            )
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Validation échouée : {exc}") from exc

    # ------------------------------------------------------------------
    # Méthodes internes
    # ------------------------------------------------------------------

    async def _enregistrer_tache_redis(self, tache: TacheValidation) -> None:
        """Enregistre une tâche dans la file Redis appropriée."""
        try:
            client = await self._nouveau_client_redis()
            await client.lpush(tache.type_file.value, tache.model_dump_json())
            await client.aclose()
        except Exception as exc:
            logger.error("Redis inaccessible, tâche non enregistrée : %s", exc)

    async def _persister_audit(self, audit: EnregistrementAudit) -> None:
        """
        Persiste l'enregistrement d'audit via src/audit.py.
        Phase 1 : JSONL local. Phase 2 : PostgreSQL sur Mac C.
        """
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
            logger.error("Audit échoué (non bloquant) : %s", exc)
            logger.info(
                "AUDIT (log only) request_id=%s agents=%s confiance=%s",
                audit.request_id,
                [a.nom_agent for a in audit.agents_executes],
                audit.niveau_confiance.value,
            )
