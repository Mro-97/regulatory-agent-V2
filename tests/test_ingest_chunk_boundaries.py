"""tests/test_ingest_chunk_boundaries.py — chunk_text ne coupe pas les mots.

Constat terrain : `chunk_text` tranchait au caractère près (`text[start:end]`),
sans égard pour les frontières de mots ni de phrases. Une fois assemblés
dans la réponse, les extraits commençaient en plein mot (« ...du présent
règlement... » tronqué à « du présent règlement » comme 1er mot d'un
chunk), rendant la lecture quasi impossible.
"""

from __future__ import annotations

from scripts.ingest import Ingester


def _ingester() -> Ingester:
    return Ingester.__new__(Ingester)


def test_chunk_text_ne_coupe_pas_les_mots() -> None:
    """Chaque chunk doit commencer et finir sur un mot entier."""
    mots = [f"mot{i:04d}" for i in range(400)]
    texte = " ".join(mots)
    chunks = _ingester().chunk_text(texte)

    assert len(chunks) >= 2, (
        "le texte doit produire plusieurs chunks pour tester la coupe"
    )
    mots_valides = set(mots)
    for chunk in chunks:
        morceaux = chunk.split()
        assert morceaux, "chunk vide inattendu"
        assert morceaux[0] in mots_valides, (
            f"chunk commence en plein mot : {morceaux[0]!r}"
        )
        assert morceaux[-1] in mots_valides, (
            f"chunk finit en plein mot : {morceaux[-1]!r}"
        )


def test_chunk_text_prefere_une_frontiere_de_phrase() -> None:
    """Sur un texte fait de phrases courtes, les chunks doivent s'aligner dessus."""
    texte = " ".join(["Texte réglementaire."] * 200)
    chunks = _ingester().chunk_text(texte)

    assert len(chunks) >= 2
    for chunk in chunks[1:]:
        # Une frontière de phrase propre donne un chunk qui commence par une
        # majuscule (ici toujours « Texte »), pas par un fragment de mot.
        assert chunk[0].isupper(), (
            f"chunk ne démarre pas sur une phrase : {chunk[:20]!r}"
        )


def test_chunk_text_couvre_le_texte_sans_grand_trou() -> None:
    """Le chevauchement doit éviter toute perte massive de contenu entre chunks."""
    texte = " ".join(f"mot{i:04d}" for i in range(400))
    chunks = _ingester().chunk_text(texte)
    longueur_couverte = sum(len(c) for c in chunks)
    # Chevauchement voulu -> couverture cumulée >= longueur du texte source.
    assert longueur_couverte >= len(texte) * 0.9
