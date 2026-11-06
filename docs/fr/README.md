<!-- i18n: source=README.md sha256=5262ab4aca2b -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — logo néon" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>Un pipeline de développement personnel qui rapproche le vendredi.</strong><br>
De la tâche au MR en brouillon : les modèles puissants planifient et relisent, les modèles économiques écrivent le code.</p>

<p align="center">Python 3.9+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · version préliminaire</p>

[English](../../README.md) · [Русский](../ru/README.md) · [简体中文](../zh-cn/README.md) · [日本語](../ja/README.md) · [한국어](../ko/README.md) · [Português (Brasil)](../pt-br/README.md) · [Español](../es/README.md) · [Deutsch](../de/README.md) · **Français** · [Bahasa Indonesia](../id/README.md)

*Ceci est une traduction de la documentation anglaise ; en cas de divergence, la version anglaise fait foi.*

---

```text
tâche → plan (modèle puissant) → revue du plan (autre fournisseur) → votre approbation
      → jalons : exécutant dans une sandbox → gate → revue de jalon → recette de l'interface dans le navigateur
      → revue finale + vérification à l'aveugle → MR en brouillon
```

Vous intervenez deux fois : vous approuvez le plan et vous acceptez le MR en brouillon. Entre ces deux points, le pipeline ne s'arrête pas : les constats de revue contestés vous sont envoyés avec le MR.

## Points clés

- **Des modèles par niveau, pas par nom.** Le profil contient un catalogue de modèles avec un fournisseur et un niveau (`frontier`, `strong`, `efficient`) pour chacun. Seuls les modèles `frontier` relisent le code, le relecteur n'est jamais plus faible que l'auteur et, si possible, il vient d'un autre fournisseur. Un modèle coûteux peut être marqué pour n'être exécuté qu'une fois par tâche.
- **Un exécutant économique avec un retour rapide.** L'exécutant lance lui-même le linter, la vérification des types et les tests dans une sandbox sans accès au réseau, et le gate ne lui transmet que les contrôles en échec.
- **Vous voyez ce qui se passe.** `friday status` affiche la phase, le modèle, depuis combien de temps l'agent est silencieux et combien chaque tâche a coûté.
- **Plusieurs tâches en parallèle.** Chaque tâche a sa propre branche et son propre worktree. Les réglages de la machine définissent combien de gates, de navigateurs et d'exécutions d'un même fournisseur peuvent tourner en même temps.

## Démarrage rapide

Il vous faut Python 3.9+, Git, ainsi que Claude Code et/ou Codex CLI. Le gate et la recette dans le navigateur nécessitent aussi Docker.

```sh
git clone git@github.com:avocawo/friday.git && cd friday
mkdir -p profiles
cp examples/models.json examples/project-profile.json profiles/
# dans profiles/project-profile.json, indiquez le chemin du dépôt, les commandes de vérification et le gate
./bin/friday doctor --profile profiles/project-profile.json
./bin/friday start TASK-1 --profile profiles/project-profile.json --brief task.md
./bin/friday approve TASK-1
./bin/friday status
```

## Commandes

| Commande | Ce qu'elle fait |
| --- | --- |
| `start KEY --profile P [--brief F]` | Crée le worktree et le plan, et fait relire le plan par un autre modèle |
| `plan KEY` | Affiche le plan avec les constats non résolus |
| `approve KEY [--no-run]` | Approuve le plan et lance le pipeline en arrière-plan |
| `status [KEY] [--json]` | Affiche ce que font les tâches et combien elles ont coûté |
| `decide KEY name "text" [--reopen M2]` | Enregistre une décision du propriétaire ; ne rouvre que les jalons indiqués |
| `run KEY` / `resume KEY` / `stop KEY` | Lance dans ce terminal, reprend, arrête |
| `archive KEY [--force]` | Supprime le worktree et la fiche ; la branche reste |
| `journal KEY` | Met à jour les journaux des tâches (Obsidian, jsonl) |
| `doctor --profile P` | Vérifie que la machine et le profil sont prêts |

Pour le détail du profil, des modèles et des limites, voir le [guide](guide.md). Pour comprendre pourquoi le pipeline est conçu ainsi, voir l'[analyse du pilote](../process-redesign.md) (en russe) et les [ADR](../adr/) (en russe).

## Développement

```sh
docker build -f Dockerfile.test -t friday-test:local .
./bin/test-offline
```

Les tests utilisent des données temporaires et de faux agents dans un conteneur sans accès au réseau.

[Guide](guide.md) · [Glossaire](GLOSSARY.md) · [Contribuer](../../CONTRIBUTING.md) · [Licence](../../LICENSE)
