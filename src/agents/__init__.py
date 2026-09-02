"""Package des agents métier (Retriever, Temporal, Explainer, Citation, Conflict).

Les agents ne dépendent QUE des modèles, des utilitaires MLX et des
prompts — jamais de l'orchestrateur ni de l'API (contrat vérifié par
import-linter dans `.importlinter`).
"""
