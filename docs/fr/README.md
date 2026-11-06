<!-- i18n: source=README.md sha256=a008f1f30688 -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — logo néon" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>Un pipeline de développement personnel qui rapproche le vendredi.</strong><br>
De la tâche au MR en brouillon : les modèles puissants planifient et relisent, les modèles économiques écrivent le code.</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · version préliminaire</p>

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

Il vous faut Python 3.11+, Git et Claude Code (Codex CLI est facultatif). Le gate et la recette dans le navigateur nécessitent aussi Docker.

```sh
uv tool install git+https://github.com/avocawo/friday   # or: pipx install git+https://github.com/avocawo/friday
mkdir -p ~/.config/friday/profiles && cd ~/.config/friday/profiles
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/models.json
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/project-profile.json
# in project-profile.json, set your project's repository, worktree_root and executor_commands
export FRIDAY_PROFILE=~/.config/friday/profiles/project-profile.json
friday doctor
friday start TASK-1 --brief task.md
friday approve TASK-1
friday status
```

Friday parle dix langues : `friday --lang ru …`, `FRIDAY_LANG=ru` ou `friday config language ru`. Les agents travaillent toujours en anglais ; ce que vous lisez est traduit. Des profils d'exemple pour les projets Node et Python se trouvent dans [examples/profiles](../../examples/profiles).

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
| `config language L` | Définit la langue dans laquelle Friday s'exprime sur cette machine |

Pour le détail du profil, des modèles et des limites, voir le [guide](guide.md). Pour comprendre pourquoi le pipeline est conçu ainsi, voir l'[analyse du pilote](../process-redesign.md) (en russe) et les [ADR](../adr/) (0001–0006 en russe).

## Développement

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

Les tests utilisent des données temporaires et de faux agents, sans réseau ni Docker. L'organisation du code est décrite dans [docs/architecture.md](../architecture.md) ; la marche à suivre pour proposer une modification est dans [CONTRIBUTING.md](../../CONTRIBUTING.md).

[Guide](guide.md) · [Glossaire](GLOSSARY.md) · [Contribuer](../../CONTRIBUTING.md) · [Licence](../../LICENSE)
