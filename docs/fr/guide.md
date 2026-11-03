<!-- i18n: source=docs/guide.md sha256=9f95975c05a4 -->
# Guide de Friday

[English](../guide.md) · [Русский](../ru/guide.md) · [简体中文](../zh-cn/guide.md) · [日本語](../ja/guide.md) · [한국어](../ko/guide.md) · [Português (Brasil)](../pt-br/guide.md) · [Español](../es/guide.md) · [Deutsch](../de/guide.md) · **Français** · [Bahasa Indonesia](../id/guide.md)

*Ceci est une traduction de la documentation anglaise ; en cas de divergence, la version anglaise fait foi.*

## Installation

1. Clonez Friday et ajoutez `bin` au `PATH`, ou définissez `FRIDAY_BIN`.
2. Installez les CLI dont les agents ont besoin : Claude Code (`claude`) pour les modèles Anthropic et Z.ai, Codex CLI (`codex`) pour les modèles OpenAI. Connectez-vous-y comme d'habitude.
3. Pour le gate et la recette dans le navigateur, installez Docker. La recette dans le navigateur nécessite aussi Chrome avec le port de débogage 9333 et une session ouverte dans l'environnement de test.
4. L'état est stocké dans `~/.local/share/friday` (modifiable avec `FRIDAY_HOME`). Les profils et les secrets n'entrent pas dans le dépôt : le dossier `profiles/` figure dans `.gitignore`.

### Plugin pour Codex et Claude Code

```sh
codex plugin marketplace add "$PWD" && codex plugin add friday@friday
claude plugin marketplace add "$PWD" && claude plugin install friday@friday --scope user
```

Le plugin apprend à l'agent à lire `status` et `plan`. Le lancement d'une tâche, son approbation et les décisions restent de votre ressort.

## Déroulement d'une tâche

1. **`start`** crée la branche `friday/KEY` à partir d'un `origin/<base_branch>` à jour ainsi qu'un worktree, fige l'attribution des rôles et écrit le plan : des jalons de 2 à 5 étapes, les fichiers, une estimation de taille, des critères de code et de navigateur, et un condensé des règles du projet pour l'exécutant. Le plan est d'abord vérifié sans modèle (taille, fichiers, ordre, critères). Ensuite, un modèle d'un autre fournisseur le relit, et le planificateur répond une seule fois aux constats : il accepte chacun d'eux ou le rejette en donnant une raison.
2. **`plan`** affiche le plan, les questions, les constats rejetés et les chevauchements de fichiers avec les autres tâches actives.
3. **`approve`** approuve le plan et lance le pipeline en arrière-plan (`--no-run` se contente d'approuver ; lancez ensuite avec `run`).
4. **Jalon.** L'exécutant travaille dans une session par jalon et fait un commit à chaque étape. Le gate s'exécute sur le commit. S'il échoue, l'exécutant ne reçoit que les sections du journal qui ont échoué. La revue de jalon examine le diff de ce jalon avec le résultat du gate. L'exécutant corrige les constats bloquants ou les conteste ; les constats contestés vous sont envoyés. Les jalons qui touchent à l'interface passent par la recette dans le navigateur, dans une preview isolée. Après deux échecs consécutifs, un modèle de niveau supérieur reprend le jalon dans une nouvelle session.
5. **Final.** La revue finale et la vérification à l'aveugle de l'ensemble du diff s'exécutent en parallèle. Les constats bloquants bénéficient d'un tour de corrections.
6. **MR en brouillon** : il est ouvert si le profil définit `delivery`. Sinon, son texte est enregistré dans `merge-request.md` dans le dossier de la tâche.

Le pipeline ne s'arrête que si le gate n'a jamais réussi, si l'exécutant n'a rien fait, si l'environnement est indisponible ou si les modèles autorisés par les règles sont épuisés. Les désaccords en revue ne l'arrêtent pas.

`decide KEY name "text" --reopen M2` enregistre votre décision. Seuls les jalons indiqués et ceux qui en dépendent sont rouverts. Sans `--reopen`, les jalons suivants tiennent compte de la décision. `resume` reprend une tâche arrêtée ou en échec. `stop` arrête une tâche en cours : les agents se terminent avec leurs processus enfants.

## Profil

Un profil est un fichier JSON. La clé `extends` inclut un autre profil (par exemple, un catalogue de modèles partagé) ; les chemins sont relatifs au fichier du profil.

| Clé | Rôle |
| --- | --- |
| `name`, `repository` | Le nom du projet et le chemin de son dépôt |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | D'où créer la branche de la tâche et où la placer ; le modèle par défaut est `friday/{key}`, la disposition est `key` ou `branch` |
| `models`, `roles` | Le catalogue de modèles et les préférences de rôles (voir ci-dessous) |
| `engineering_policy` | Règles pour le planificateur et les revues |
| `rules_docs` | Documents contenant les règles du projet. Friday en produit un condensé unique pour le planificateur et le relecteur du plan (par défaut `AGENTS.md`, `CLAUDE.md`, `docs/*.md`). Le condensé est stocké par hachage du contenu et réutilisé par les tâches tant que les documents ne changent pas ; le planificateur ne lit pas les documents en entier |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | Une carte du dépôt dans le prompt du planificateur et du relecteur du plan, pour qu'ils n'explorent pas la structure à coups d'appels d'outils. `none` (par défaut) : pas de carte. `builtin` : les fichiers suivis avec leur nombre de lignes. `graphify` : en plus, les symboles de chaque fichier et le nombre de fichiers qui l'importent, tirés d'un graphe [graphify](https://github.com/Graphify-Labs/graphify) (`uv tool install 'graphifyy[mcp]==0.9.100'` ; il se contente d'analyser le code, aucun modèle n'est utilisé ; le graphe est stocké dans `FRIDAY_HOME/maps`, pas dans le dépôt). Lors d'une mesure sur CS-2, la carte n'a pas rendu la planification moins coûteuse (sans carte 6,19 $, `builtin` 7,08 $, `graphify` 7,97 $, une exécution chacun), elle est donc désactivée par défaut. La carte est mise en cache par commit. Si elle dépasse `repo_map_budget` caractères (60000), on la raccourcit d'abord en supprimant les symboles, puis en réduisant les dossiers. `repo_map_mcp: true` (uniquement avec `graphify`) donne aux deux rôles des requêtes sur le graphe : `query_graph`, `get_node`, `get_neighbors`, `shortest_path` ; non vérifié en conditions réelles sur Codex. Le relecteur du plan reçoit le texte actuel des fichiers cités dans le plan (jusqu'à 40000 caractères) |
| `approval` | `owner` (par défaut) : le plan attend `approve`. `auto` : le plan est approuvé automatiquement après la revue et le pipeline démarre aussitôt ; le journal indique que le plan a été approuvé automatiquement |
| `limits` | `stage_files` et `stage_lines` : les limites d'une étape (10 et 500) |
| `worktree_setup` | Commandes que Friday exécute dans un nouveau worktree avant la planification (dépendances et similaires) : des tableaux d'arguments avec les substitutions `{repository}` et `{worktree}` ; elles doivent pouvoir être rejouées sans effet de bord. Exemple pour npm sous macOS : `["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | Commandes que l'exécutant peut lancer lui-même, sous la forme exacte : `"python3 -m pytest"`, pas `python` |
| `executor_deny_read` | Chemins supplémentaires que le shell de l'exécutant ne peut pas lire |
| `milestone_rounds` | Combien de tours d'exécutant un jalon obtient (4) |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | La commande de vérification dans Docker ; `git-diff` compare avec la base de la tâche |
| `environment_errors` | Motifs de lignes de journal qui signalent une panne d'environnement, et non une panne du code |
| `browser`, `preview`, `behavior_test_policy` | L'environnement de test, la preview et les règles de la recette dans le navigateur |
| `tracker` | Yandex Tracker (API ou MCP), utilisé pour lire la description de la tâche |
| `journal` | Où écrire le journal de la tâche : `[{"kind": "markdown", "dir": "..."}, {"kind": "jsonl", "dir": "..."}]` ; `journal_dir` est une forme abrégée pour un seul journal markdown |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}` et la clé `github.repository` ou `gitlab.base_url` + `gitlab.project` |

### Modèles et rôles

```json
"models": {
  "fable": {"model": "claude-fable-5-1", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
  "opus-high": {"model": "claude-opus-5-5", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "effort": "high"},
  "glm-high": {"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong", "effort": "high",
               "token_file": "~/.config/friday/zai.env", "token_key": "ZAI_API_KEY"}
},
"roles": {"plan": ["fable"], "develop": ["glm-high"], "review": ["opus-high"]}
```

- `driver` : ce qui exécute le modèle, `claude` ou `codex`. Z.ai passe par Claude Code ; le jeton est lu dans un fichier accessible uniquement au propriétaire.
- `tier` : `frontier`, `strong` ou `efficient` ; `strength` ordonne les modèles au sein d'un niveau.
- `effort` : la profondeur de raisonnement (`low` … `max`) ; un même modèle peut être décrit plusieurs fois avec des efforts différents.
- `scarce` : un modèle coûteux ; il est utilisé une fois par tâche si les règles permettent de se passer de lui.
- `price` : le prix par million de tokens selon le type (`input`, `output`, `cache_read`, `cache_write`), utilisé pour estimer les dépenses quand le fournisseur ne communique pas de prix.

Rôles : `plan`, `plan-review`, `develop`, `review`, `final-review`, `blind-code`, `browser`. Les listes de `roles` sont des préférences : si la liste ne contient aucun modèle adapté, Friday en prend un autre dans le catalogue. Règles strictes : seuls les modèles `frontier` relisent le code, le relecteur n'est pas plus faible que l'auteur du travail, et la vérification à l'aveugle n'est pas faite par l'auteur du code. Règles souples, dans l'ordre : pas le même modèle que l'auteur ; un modèle coûteux une seule fois ; un fournisseur différent ; la vérification à l'aveugle pas par l'auteur du plan. Une règle souple ne peut être enfreinte que s'il n'y a pas d'autre solution, et l'infraction est consignée dans l'historique de la tâche.

Un exemple d'attribution des rôles se trouve dans [examples/models.json](../../examples/models.json).

## Journal

La fiche de la tâche est la seule source de vérité. Les journaux la reflètent et se mettent à jour après les événements importants : le plan est prêt et approuvé, un jalon est terminé ou en échec, une décision du propriétaire, la revue finale, le MR, un arrêt. Pour les mettre à jour à la main, lancez `friday journal KEY`.

- `markdown` : une page `KEY.md` et une page de synthèse commune qui liste toutes les tâches. Pour Obsidian, indiquez le dossier du coffre (vault) : les pages ont un front matter (clé, état, dépenses, le tag `friday`) et les tâches sont reliées par des wiki links. Une page de tâche contient le plan avec ses hypothèses et ses constats non résolus, les jalons avec le résumé de l'exécutant et les commits, les constats contestés, les décisions, les dépenses par rôle et la chronologie.
- `jsonl` : tous les événements de la tâche, pour l'analyse dans des tableurs ou des tableaux de bord.

Les modèles sont du Markdown avec les substitutions `{{key}}`, `{{state}}`, `{{status}}`, `{{plan}}`, `{{milestones}}`, `{{disputes}}`, `{{decisions}}`, `{{costs}}`, `{{timeline}}`, `{{notes}}`. Friday prend le modèle dans le champ `template` de la cible, puis `.friday/task.md` dans le dépôt du projet, puis celui qui est intégré ([friday/templates](../../friday/templates)). Le texte entre `<!-- friday:notes -->` et `<!-- /friday:notes -->` vous appartient : il est conservé à chaque mise à jour. Friday n'écrase pas une page qu'il n'a pas écrite.

## Réglages de la machine

`FRIDAY_HOME/machine.json` définit les ressources partagées par toutes les tâches :

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

Par défaut, un gate et deux navigateurs tournent en même temps, et les fournisseurs ne sont pas limités. Les quotas des fournisseurs sont suivis automatiquement : une exécution qui atteint une limite enregistre quand le quota revient, et d'ici là le fournisseur est écarté lors du choix des modèles.

## Limites

- **Sandbox de l'exécutant.** Le shell de l'exécutant (Claude Code) s'exécute dans une sandbox : pas de réseau, pas de lecture de `~/.ssh`, `~/.aws`, `~/.config` et similaires, écriture uniquement dans le worktree et dans un dossier temporaire, et aucune exécution hors de la sandbox. Les commandes qui ne figurent pas dans `executor_commands` et ne sont pas des commandes git sont refusées. Codex s'exécute dans sa propre sandbox avec les mêmes limites.
- **Les relecteurs** ne font que lire. La vérification à l'aveugle s'exécute dans un dossier vide.
- **Le gate** s'exécute dans un conteneur sans accès au réseau, sur un instantané du commit monté en lecture seule.
- **Le navigateur** ne reçoit que les outils de Friday pour une seule preview ; contourner l'autorisation et lire les en-têtes des requêtes autorisées sont interdits.
- **Publication.** Friday ne pousse que la branche de sa propre tâche et n'ouvre qu'un MR/PR en brouillon. Il n'écrase pas la branche de quelqu'un d'autre, ne fusionne pas et ne déploie pas.
- **Pas encore vérifié en conditions réelles :** les rôles sur Codex, la recette dans le navigateur et la publication sur GitLab n'ont été vérifiés que par des tests avec des agents factices.
