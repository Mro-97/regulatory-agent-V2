#!/usr/bin/env python3
"""Ingestion d'un JSON réglementaire dans Qdrant avec chunking (600, overlap 50)."""

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import cfg
from src.models import DocumentReglementaire, MetadonneesChunk

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Paramètres de chunking (modifiables)
CHUNK_SIZE = 600
OVERLAP = 50

# Fenêtre de recherche (en caractères, en arrière depuis la coupe brute) d'une
# frontière propre — fin de phrase, sinon espace — pour ne jamais couper un
# chunk en plein mot ni en plein milieu d'une phrase.
_LARGEUR_FRONTIERE = 80
_FINS_DE_PHRASE = (". ", "? ", "! ", ".\n", "?\n", "!\n", "\n\n")

# Namespace pour dériver un id de point Qdrant stable depuis un chunk_id.
_NS_CHUNK = uuid.uuid5(uuid.NAMESPACE_URL, "regulatory-agent/chunk")


def _filtre_selector_document(document_id: str) -> Any:
    """Sélecteur Qdrant ciblant tous les points d'un `document_id` donné."""
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        FilterSelector,
        MatchValue,
    )

    return FilterSelector(
        filter=Filter(
            must=[
                FieldCondition(key="document_id", match=MatchValue(value=document_id))
            ]
        )
    )


def _collection_absente(exc: Exception) -> bool:
    """True si `exc` signifie « la collection n'existe pas encore ».

    Distingue le seul cas où « 0 chunk » est une réponse honnête (première
    ingestion) de toute autre panne Qdrant, qui doit remonter : cf.
    `Ingester.compter_chunks_existants`. Le motif reste étroit : un simple
    « 404 » trouvé dans un message d'erreur quelconque réintroduirait le 0
    silencieux que ce contrôle existe pour empêcher.
    """
    message = str(exc).lower()
    return any(
        indice in message
        for indice in (
            "not found",
            "doesn't exist",
            "does not exist",
            "collection not found",
        )
    )


class Ingester:  # noqa: D101
    def __init__(  # noqa: D107
        self,
        collection_name: str = "regulatory_chunks",
        recreate: bool = False,
    ) -> None:
        # F1 : respecter cfg.qdrant_https + cfg.qdrant_api_key. `url=` seul
        # hardcodait http:// et ignorait la clé Qdrant même si présente.
        scheme = "https" if cfg.qdrant_https else "http"
        self.client = QdrantClient(
            url=f"{scheme}://{cfg.qdrant_host}:{cfg.qdrant_port}",
            api_key=cfg.qdrant_api_key or None,
        )
        self.collection_name = collection_name
        self.embedding_model = self._load_embedding_model()

        if recreate:
            self._recreate_collection()

    def _load_embedding_model(self) -> Any:
        """Backend d'embedding — le MÊME que le retriever (source unique).

        `cfg.modele_embedding` (bge-m3, dim `cfg.embedding_dimension`) au
        lieu d'un `all-MiniLM-L6-v2` local 384-dim : ingérer avec un modèle
        différent de la recherche produisait des vecteurs incompatibles
        avec la collection.
        """
        from src.mlx_embedding import get_embedding

        return get_embedding(cfg.modele_embedding)

    def _recreate_collection(self) -> None:
        dim = cfg.embedding_dimension
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
            logger.info("Collection '%s' supprimée", self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        logger.info("Collection '%s' créée (dim=%d)", self.collection_name, dim)

    def embed_chunk(self, text: str) -> list[float]:  # noqa: D102
        vec = self.embedding_model.encode(text)
        # SentenceTransformer renvoie un ndarray ; les mocks de tests renvoient
        # directement une liste. Accepter les deux sans conversion agressive.
        return vec.tolist() if hasattr(vec, "tolist") else list(vec)

    def _frontiere_propre(self, text: str, position: int) -> int:
        """Recule `position` jusqu'à la fin de phrase la plus proche.

        Cherche dans `_LARGEUR_FRONTIERE` caractères en arrière une fin de
        phrase (`. `, `? `, `! `, saut de paragraphe) ; à défaut le dernier
        espace ; à défaut `position` inchangée (mot unique trop long, cas
        pathologique). Sans ce calage, `chunk_text` coupait au caractère
        près : un chunk pouvait commencer en plein mot ou en plein milieu
        d'une phrase (ex. « ...du présent règlement... » tronqué en « du
        présent règlement » comme premier mot d'un chunk), rendant les
        extraits illisibles une fois assemblés dans la réponse.
        """
        limite = max(0, position - _LARGEUR_FRONTIERE)
        fenetre = text[limite:position]
        for motif in _FINS_DE_PHRASE:
            idx = fenetre.rfind(motif)
            if idx != -1:
                return limite + idx + len(motif)
        idx = fenetre.rfind(" ")
        if idx != -1:
            return limite + idx + 1
        return position

    def chunk_text(self, text: str) -> list[str]:
        """Découpe un texte en chunks de ~CHUNK_SIZE caractères, chevauchement OVERLAP.

        Les bornes sont calées sur une frontière propre (`_frontiere_propre`)
        pour qu'un chunk ne commence ni ne finisse en plein mot ou en plein
        milieu d'une phrase.
        """
        chunks = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + CHUNK_SIZE, n)
            if end < n:
                propre = self._frontiere_propre(text, end)
                if propre > start:
                    end = propre
            morceau = text[start:end].strip()
            if morceau:
                chunks.append(morceau)
            if end >= n:
                break
            nouveau_start = max(start + 1, end - OVERLAP)
            # Recale aussi le début du chunk suivant sur un mot entier.
            espace = text.find(" ", nouveau_start)
            if 0 <= espace - nouveau_start < _LARGEUR_FRONTIERE:
                nouveau_start = espace + 1
            start = nouveau_start
        return chunks

    def chunk_document(self, doc: DocumentReglementaire) -> list[MetadonneesChunk]:  # noqa: D102
        chunks = []
        mini = cfg.ingest_taille_min_chunk
        for chapitre in doc.chapitres:
            for article in chapitre.articles:
                # Découper l'article en chunks
                text_chunks = self.chunk_text(article.texte)
                for idx, chunk_text in enumerate(text_chunks):
                    if len(chunk_text.strip()) < mini:
                        logger.debug(
                            "Chunk écarté (%d < %d car.) : %s_%s_part%d",
                            len(chunk_text.strip()),
                            mini,
                            doc.id,
                            article.id,
                            idx + 1,
                        )
                        continue
                    chunk = MetadonneesChunk(
                        chunk_id=f"{doc.id}_{article.id}_part{idx + 1}",
                        document_id=doc.id,
                        chapitre_id=chapitre.id,
                        article_id=article.id,
                        source=doc.source,
                        themes=doc.themes,
                        valid_from=article.validite.valid_from,
                        valid_to=article.validite.valid_to,
                        texte_chunk=chunk_text,
                        position_dans_article=idx,
                    )
                    chunks.append(chunk)
        return chunks

    def ingest_document(self, doc: DocumentReglementaire) -> int:
        """Ré-indexe `doc` : purge ses points existants, puis chunk + embed + upsert.

        Idempotent : deux appels successifs sur le même document laissent
        la collection identique (les `id` de points sont dérivés du
        `chunk_id` — cf. `_chunk_vers_point` — et la purge en tête retire
        les chunks devenus orphelins si le découpage a changé).
        """
        self.supprimer_chunks_document(doc.id)
        chunks = self.chunk_document(doc)
        logger.info("%d chunks générés pour %s", len(chunks), doc.id)
        chunks_traites = self._appliquer_sanitizer(chunks)
        points = [self._chunk_vers_point(c) for c in chunks_traites]
        if not points:
            logger.warning("Aucun point à indexer pour %s", doc.id)
            return 0
        self.client.upsert(collection_name=self.collection_name, points=points)
        logger.info("%d points indexés dans Qdrant", len(points))
        return len(points)

    def _appliquer_sanitizer(
        self, chunks: list[MetadonneesChunk]
    ) -> list[MetadonneesChunk]:
        """Filtre + annote les chunks selon `cfg.ingest_mode_sanitizer`."""
        from src.ingest_sanitizer import ModeSanitizer, appliquer_politique

        try:
            mode = ModeSanitizer(cfg.ingest_mode_sanitizer)
        except ValueError:
            logger.warning(
                "ingest_mode_sanitizer='%s' inconnu, fallback 'annoter'.",
                cfg.ingest_mode_sanitizer,
            )
            mode = ModeSanitizer.ANNOTER
        conserves: list[MetadonneesChunk] = []
        for chunk in chunks:
            traite = appliquer_politique(chunk.texte_chunk, mode, chunk.chunk_id)
            if traite is None:
                continue
            if traite != chunk.texte_chunk:
                chunk = chunk.model_copy(update={"texte_chunk": traite})
            conserves.append(chunk)
        return conserves

    def _chunk_vers_point(self, chunk: Any) -> PointStruct:
        """Convertit un chunk en PointStruct Qdrant (id déterministe = uuid5(chunk_id)).

        L'`id` dérive du `chunk_id` stable (`{doc}_{article}_part{n}`) :
        ré-ingérer un chunk inchangé écrase le point au lieu d'en créer
        un doublon (cause des ~14 k doublons exacts dans la collection).
        """
        return PointStruct(
            id=str(uuid.uuid5(_NS_CHUNK, chunk.chunk_id)),
            vector=self._encoder_chunk(chunk),
            payload={
                **chunk.model_dump(mode="json"),
                "original_id": chunk.chunk_id,
            },
        )

    def _encoder_chunk(self, chunk: Any) -> list[float]:
        """Embedding d'un chunk via le chemin batch si le backend le propose.

        `MLXEmbedding.encode_batch` encode par lots et évite un appel
        unitaire par chunk ; le repli sur `encode` couvre les backends de
        test qui n'exposent que la méthode unitaire.
        """
        encoder_lot = getattr(self.embedding_model, "encode_batch", None)
        if callable(encoder_lot):
            vecteurs = encoder_lot([chunk.texte_chunk])
            if vecteurs:
                return list(vecteurs[0])
        return self.embed_chunk(chunk.texte_chunk)

    def compter_chunks_existants(self, document_id: str) -> int:
        """Compte le nombre de chunks déjà présents pour un document_id donné.

        Utilisé par l'orchestrateur pour décider si une nouvelle ingestion
        doit renvoyer 409 (déjà indexé) ou remplacer les points existants.

        Une ERREUR de comptage n'est PAS « zéro chunk » : `supprimer_chunks_document`
        se sert de ce nombre pour décider s'il doit purger, et un 0 mensonger
        faisait sauter la purge — les chunks de la version précédente
        survivaient alors à une ré-ingestion (le retrieval filtre par dates de
        validité, donc ils restaient répondables). Seule l'absence de
        collection est interprétée comme « 0 chunk » ; toute autre erreur
        remonte à l'appelant.

        Raises:
            VectorStoreError: Qdrant injoignable ou en erreur.
        """
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        from src.errors import VectorStoreError

        filtre = Filter(
            must=[
                FieldCondition(key="document_id", match=MatchValue(value=document_id))
            ]
        )
        try:
            resultat = self.client.count(
                collection_name=self.collection_name,
                count_filter=filtre,
                exact=True,
            )
            return int(resultat.count)
        except Exception as exc:
            if _collection_absente(exc):
                logger.debug(
                    "compter_chunks_existants(%s) : collection absente → 0.",
                    document_id,
                )
                return 0
            raise VectorStoreError(self.collection_name, cause=str(exc)) from exc

    def supprimer_chunks_document(self, document_id: str) -> int:
        """Supprime tous les points Qdrant d'un `document_id` (retourne le nb)."""
        avant = self.compter_chunks_existants(document_id)
        if avant == 0:
            return 0
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=_filtre_selector_document(document_id),
        )
        logger.info("Supprimé %d chunk(s) pour document_id=%s", avant, document_id)
        return avant

    def ingest_json(self, json_path: Path) -> None:
        """Chemin d'entrée CLI : charge un JSON puis appelle `ingest_document`."""
        with json_path.open(encoding="utf-8") as f:
            data = json.load(f)
        doc = DocumentReglementaire(**data)
        logger.info("Document chargé : %s - %s", doc.id, doc.titre)
        self.ingest_document(doc)

    def ingest_dossier(self, dossier: Path) -> None:
        """Ingère tous les `*.json` d'un dossier avec un seul Ingester (1 modèle).

        Continue sur erreur ; récapitule à la fin. Idempotent
        (`ingest_document` purge le document avant réinsertion).
        """
        fichiers = sorted(dossier.glob("*.json"))
        if not fichiers:
            logger.warning("Aucun JSON dans %s", dossier)
            return
        total_chunks, echecs = 0, []
        for i, chemin in enumerate(fichiers, 1):
            logger.info("[%d/%d] %s", i, len(fichiers), chemin.name)
            try:
                with chemin.open(encoding="utf-8") as f:
                    doc = DocumentReglementaire(**json.load(f))
                total_chunks += self.ingest_document(doc)
            except Exception:
                logger.exception("  échec %s", chemin.name)
                echecs.append(chemin.name)
        logger.info(
            "Terminé : %d/%d documents, %d chunks%s",
            len(fichiers) - len(echecs),
            len(fichiers),
            total_chunks,
            f" — échecs : {', '.join(echecs)}" if echecs else "",
        )


def main() -> None:  # noqa: D103
    parser = argparse.ArgumentParser(
        description="Ingérer un JSON réglementaire dans Qdrant"
    )
    entree = parser.add_mutually_exclusive_group(required=True)
    entree.add_argument("--json", help="Chemin vers un fichier JSON")
    entree.add_argument("--dir", help="Dossier de *.json à ingérer en lot")
    parser.add_argument(
        "--collection", default="regulatory_chunks", help="Nom de la collection Qdrant"
    )
    parser.add_argument("--recreate", action="store_true", help="Recréer la collection")
    args = parser.parse_args()

    ingester = Ingester(collection_name=args.collection, recreate=args.recreate)
    if args.dir:
        ingester.ingest_dossier(Path(args.dir))
    else:
        ingester.ingest_json(Path(args.json))


if __name__ == "__main__":
    main()
