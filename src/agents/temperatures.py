"""src/agents/temperatures.py — Températures d'inférence, par RÔLE.

Une température n'est pas un réglage global du système : c'est un choix lié à
la nature du travail demandé au modèle. Deux régimes seulement, et ils ne
doivent pas être confondus :

- `RAISONNEMENT` (0.0) : analyse de conflits, vérification d'ancrage des
  citations, filtrage temporel. Ces agents comparent, classent et vérifient :
  une sortie qui varie d'un appel à l'autre rendrait le résultat
  irréproductible, donc invérifiable. Une réponse identique pour une entrée
  identique est une propriété de sûreté, pas une préférence.
- `REDACTION` (0.1) : l'Explainer rédige une synthèse en langage naturel. Un
  peu de variabilité rend le texte moins mécanique sans dégrader l'ancrage,
  qui est contrôlé séparément par l'agent Citation.

Ce module est un feuille : il ne dépend de rien, et c'est ce qui permet aux
agents de partager ces valeurs sans coupler leurs modules entre eux.
"""

from __future__ import annotations

# Analyse, comparaison, vérification : la reproductibilité prime.
TEMPERATURE_RAISONNEMENT = 0.0

# Rédaction : variabilité légère acceptée, l'ancrage étant vérifié à part.
TEMPERATURE_REDACTION = 0.1
