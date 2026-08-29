# prompts/ — gabarits LLM versionnés

Skill `regulatory-code-standards §11` : **aucun prompt en dur dans le code**.
Chaque prompt vit dans un fichier versionné `prompts/<agent>/<tache>.v<N>.md`
et est chargé par identifiant via `src.prompts_loader.charger_prompt()`.

## Format d'un fichier

```
# system
<texte du prompt système, une ou plusieurs lignes>

# user
<texte du prompt utilisateur, avec placeholders `$variable`>
```

- Les sections sont délimitées par `# system` et `# user` (en début de ligne).
- Les placeholders utilisent la syntaxe `string.Template` (`$nom` /
  `${nom}`) pour ne pas collisionner avec les accolades des exemples JSON.
- L'identifiant complet est le couple `("<agent>/<tache>", <version>)`,
  ex. `("temporal/annoter", 1)`.

## Versionnement

- Créer une nouvelle version `.v<N+1>.md` quand un prompt change en
  production ; ne pas éditer une version en cours d'usage.
- L'audit de chaque appel LLM doit tracer l'identifiant et la version
  (voir `SortieAgent.contenu["prompt"]` dans `src/orchestrator_pipeline.py`).
