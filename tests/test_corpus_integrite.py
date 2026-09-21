"""tests/test_corpus_integrite.py — critère de détection du texte inversé.

Le critère est partagé par l'extraction PDF (réparation à la source),
l'ingestion (garde-fou) et `scripts/diagnostic_corpus.py` (inventaire et
purge) : ces tests verrouillent les deux côtés de la frontière — détecter
le texte inversé, et NE PAS signaler du contenu légitime (français ou
anglais), car un faux positif ferait perdre du corpus valide.
"""

from __future__ import annotations

import pytest

from src.corpus_integrite import (
    MIN_INVERSIONS,
    est_inverse,
    mesurer,
    reparer,
)

TEXTE_NORMAL = (
    "L'article 33 du reglement prevoit que le responsable du traitement "
    "notifie la violation de donnees a l'autorite de controle dans les "
    "soixante-douze heures. Cette notification est accompagnee des "
    "informations que la commission exige pour la securite des personnes. "
    "Les donnees sont conservees dans un registre que la commission tient "
    "a jour; il est mis a la disposition du public par l'autorite."
)
TEXTE_INVERSE = TEXTE_NORMAL[::-1]

# Mots dont l'inversion sert à fabriquer des textes « à l'envers » de test.
_NEUF_MOTS = ("de", "la", "les", "des", "du", "et", "un", "une", "est")

# Extrait réel du corpus (REACH_1907_2006, art_141) — page PDF pivotée.
TEXTE_REACH_INVERSE = (
    "QIFICÉPS\nSELGÈR\nSEÉGIXE\nDRADNATS\nSNOITAMROFNI\n1\nENNOLOC\nAL\nÀ\n"
    "TROPPAR\nRAP\n:séirporppa\ntnos\neénatuc\neiov\nrap\nsiasse\nseL\n.3.5.8\n"
    "eénatuc\neiov\nraP\n.3.5.8\n;elbaborpmi\ntse\necnatsbus\nal\ned\n"
    "noitalahni'l\nis\n)1\nte\n;elbaborp\ntse\nnoitasilitu'l\ned\nuo/te\n"
    "noitcudorp\nal\ned\nsrol\nénatuc\ntcatnoc\nnu\nis\n)2\ntse\nli'uq\nresn"
)

TEXTE_ANGLAIS = (
    "The controller shall notify the supervisory authority of a personal "
    "data breach without undue delay and, where feasible, not later than "
    "72 hours after having become aware of it. The processor shall assist "
    "the controller in ensuring compliance with the obligations pursuant "
    "to Articles 32 to 36, taking into account the nature of processing."
)


class TestDetection:
    def test_texte_francais_inverse_detecte(self) -> None:
        inverses, normaux = mesurer(TEXTE_INVERSE)
        assert inverses >= MIN_INVERSIONS
        assert normaux == 0
        assert est_inverse(TEXTE_INVERSE)

    def test_extrait_reel_du_corpus_detecte(self) -> None:
        """L'extrait REACH qui a motivé le critère reste détecté."""
        assert est_inverse(TEXTE_REACH_INVERSE)

    def test_texte_francais_normal_non_detecte(self) -> None:
        inverses, _ = mesurer(TEXTE_NORMAL)
        assert not est_inverse(TEXTE_NORMAL)
        assert inverses < MIN_INVERSIONS

    def test_texte_anglais_non_detecte(self) -> None:
        """Faux positif à éviter : NIST/ENISA sont légitimement en anglais."""
        assert not est_inverse(TEXTE_ANGLAIS)

    def test_seuil_min_inversions(self) -> None:
        """Sous le seuil : rien ; au seuil (sans mot normal) : détecté."""
        neuf = " ".join(mot[::-1] for mot in _NEUF_MOTS)
        assert mesurer(neuf) == (9, 0)
        assert not est_inverse(neuf)
        assert est_inverse(f"{neuf} ed")

    def test_dominance_requise(self) -> None:
        """Beaucoup d'inversions mais autant de mots normaux : pas inversé."""
        dix = " ".join(mot[::-1] for mot in (*_NEUF_MOTS, "ou"))
        assert not est_inverse(f"{dix} {TEXTE_NORMAL}")

    def test_texte_vide_non_detecte(self) -> None:
        assert not est_inverse("")
        assert mesurer("") == (0, 0)


class TestReparation:
    def test_reparer_restitue_l_original(self) -> None:
        assert reparer(TEXTE_INVERSE) == TEXTE_NORMAL

    def test_texte_repare_n_est_plus_detecte(self) -> None:
        assert not est_inverse(reparer(TEXTE_INVERSE))
        assert not est_inverse(reparer(TEXTE_REACH_INVERSE))

    @pytest.mark.parametrize("texte", ["", "Article 33", TEXTE_NORMAL])
    def test_reparer_est_involutif(self, texte: str) -> None:
        assert reparer(reparer(texte)) == texte
