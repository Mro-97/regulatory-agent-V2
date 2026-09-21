"""src/corpus_integrite.py — détection du texte lu à l'envers.

Contexte : certaines pages du Journal officiel REACH (règlement 1907/2006)
sont pivotées à 180 degrés. L'extraction PDF les a lues à l'envers et ces
chunks ont été indexés tels quels : leurs vecteurs attirent des requêtes sans
aucun rapport. Mesure du 2026-09-21 : 91 chunks sur 1856 pour REACH (4,9 %),
aucun autre document touché, et aucun faux positif sur les 21 467 chunks de la
collection.

Le texte inversé se reconnaît à ses mots français courants écrits à l'envers
(« ed » pour « de », « al » pour « la »). Le critère est volontairement STRICT
(au moins `MIN_INVERSIONS` inversions ET `RATIO_DOMINANCE` fois plus
d'inversions que de mots normaux) : un seuil plus laxiste signalait du texte
anglais légitime (NIST, ENISA) et aurait fait perdre du bon contenu.

Ce module est PUR (stdlib uniquement) pour être partagé par les trois étages :
l'extraction PDF répare à la source, l'ingestion écarte ce qui passerait
encore, et `scripts/diagnostic_corpus.py` inventorie ou purge l'existant.
"""

from __future__ import annotations

import re

# Mots très fréquents en français juridique, et leur inversion.
COURANTS = (
    "de",
    "la",
    "le",
    "les",
    "des",
    "du",
    "et",
    "un",
    "une",
    "est",
    "pour",
    "dans",
    "que",
    "qui",
    "sur",
    "par",
    "avec",
    "ne",
    "pas",
    "au",
    "aux",
    "en",
    "il",
    "elle",
    "sont",
    "cette",
    "son",
    "sa",
    "ses",
    "ou",
    "article",
    "reglement",
    "commission",
)
_INVERSES = frozenset(mot[::-1] for mot in COURANTS)
_JETON = re.compile(r"[a-zA-Zà-ÿ']{2,}")

# Critères stricts : voir la docstring — un seuil laxiste crée des faux positifs.
MIN_INVERSIONS = 10
RATIO_DOMINANCE = 4


def mesurer(texte: str) -> tuple[int, int]:
    """Compte dans `texte` les (mots inversés, mots français normaux)."""
    jetons = [jeton.lower() for jeton in _JETON.findall(texte)]
    inverses = sum(1 for jeton in jetons if jeton in _INVERSES)
    normaux = sum(1 for jeton in jetons if jeton in COURANTS)
    return inverses, normaux


def est_inverse(texte: str) -> bool:
    """True si `texte` est du français lu à l'envers (critère strict)."""
    inverses, normaux = mesurer(texte)
    return inverses >= MIN_INVERSIONS and inverses > normaux * RATIO_DOMINANCE


def reparer(texte: str) -> str:
    """Remet à l'endroit un texte lu à l'envers.

    Une page pivotée sort intégralement inversée, caractère par caractère :
    l'inversion complète restitue l'original. Vérifié sur les 91 chunks
    corrompus du corpus — les 91 redeviennent du français normal, 0 échec.
    """
    return texte[::-1]
