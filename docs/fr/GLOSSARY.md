<!-- i18n: source=GLOSSARY.md sha256=fd78b7d8bdb2 -->
# Friday

[English](../../GLOSSARY.md) · [Русский](../ru/GLOSSARY.md) · [简体中文](../zh-cn/GLOSSARY.md) · [日本語](../ja/GLOSSARY.md) · [한국어](../ko/GLOSSARY.md) · [Português (Brasil)](../pt-br/GLOSSARY.md) · [Español](../es/GLOSSARY.md) · [Deutsch](../de/GLOSSARY.md) · **Français** · [Bahasa Indonesia](../id/GLOSSARY.md)

*Ceci est une traduction de la documentation anglaise ; en cas de divergence, la version anglaise fait foi.*

Un pipeline de développement personnel : il mène une tâche du plan jusqu'au MR en brouillon, en s'appuyant sur des modèles de différents fournisseurs et en vérifiant leur travail.

## Travail

**Tâche** :
Une unité de travail pour le propriétaire, avec sa propre fiche, sa branche et son worktree ; correspond en général à un ticket du tracker.
_Avoid_: mission, job

**Plan** :
La liste des jalons et des étapes d'une tâche, avec les critères d'acceptation, approuvée par le propriétaire.

**Jalon** :
Un groupe de 2 à 5 étapes consécutives qui passe le gate et la revue d'un seul bloc.
_Avoid_: phase, palier

**Étape** :
La plus petite partie d'un plan ; elle se termine par un seul commit.
_Avoid_: pas, stage

**Critère** :
Une condition d'acceptation vérifiable pour une étape ; c'est soit un critère de code (vérifié sur le diff), soit un critère de comportement (observé dans le navigateur).

**Profil** :
Le fichier JSON qui configure Friday pour un projet (dépôt, modèles, rôles, gate, navigateur, tracker, journal, livraison) ; il peut étendre un autre profil via la clé `extends`.

## Rôles

**Propriétaire** :
La personne qui approuve le plan, accepte le MR en brouillon et tranche les constats contestés.
_Avoid_: opérateur, utilisateur

**Planificateur** :
Le modèle qui écrit le plan.

**Exécutant** :
Le modèle qui écrit le code d'un jalon.
_Avoid_: développeur

**Relecteur** :
Le modèle qui relit le plan, un jalon ou le diff final et rend un verdict sur les critères.

**Niveau de modèle** :
La classe de capacité d'un modèle : `frontier`, `strong` ou `efficient`.

**Attribution des rôles** :
L'affectation de modèles précis aux rôles d'une tâche, fixée avant l'approbation du plan.

## Contrôles

**Gate** :
Des contrôles déterministes, sans modèle, exécutés dans un conteneur isolé sur un commit précis.
_Avoid_: CI, validation

**Revue de jalon** :
Une relecture du diff du jalon, avec le journal du gate, par un relecteur d'un autre fournisseur.

**Revue finale** :
Une relecture du diff complet de la tâche avant le MR, y compris la vérification à l'aveugle du code.

**Recette dans le navigateur** :
L'observation des critères de comportement d'un jalon dans le navigateur, sur un environnement de test local.
_Avoid_: blind-behavior

**Constat contesté** :
Un constat de relecture auquel l'exécutant s'est opposé et qui a été envoyé au propriétaire par le journal.

## Données

**Fiche** :
L'état machine d'une tâche dans Friday : événements, jalons, verdicts, dépenses.

**Journal** :
L'historique lisible d'une tâche dans Obsidian, construit à partir de la fiche avec des modèles.
