# Incident — la base réelle a migré pendant la Phase 37

**Date** : 29 août 2026
**Nature** : incident d'intégrité procédurale. Aucun défaut fonctionnel, aucune
perte de donnée.
**État** : clos, contenu, nouvelle baseline établie.

---

## Ce qui s'est passé

Pendant la Phase 37, deux vérifications ponctuelles ont été lancées ainsi :

```
python -c "import main"
```

sans `NOVA_DB`. `main.py` exécute `init_db()` et `migrate_schema()` **à
l'import** : la base réelle `nova.db` a donc reçu les migrations additives des
Phases 36 et 37 — tables SCIM, `deployment_profiles`, et la reconstruction
tenant-scoped de `federated_identities`.

C'est la **deuxième occurrence de la même classe d'incident**. La première, en
Phase 30, avait produit le garde-fou `recipe_env.py`.

---

## Ce qui a été vérifié

Vérifications faites en **lecture seule** (`file:nova.db?mode=ro`), sans jamais
importer `main` :

| Contrôle | Résultat |
| --- | --- |
| `PRAGMA integrity_check` | `ok` |
| Organisation présente | oui |
| Utilisateur présent | oui |
| Identité fédérée présente | oui |
| `federated_identities.user_id` == `users.user_id` | oui |
| `federated_identities.organization_id` == `users.organization_id` | oui |
| Lignes dans `deployment_profiles` | 0 — aucune donnée de test n'a été écrite |

L'empreinte de la base est identique avant et après ces lectures : la
vérification elle-même n'a rien modifié.

Les migrations appliquées sont exactement celles que le service appliquerait de
toute façon à son prochain démarrage. Le lien SSO de l'unique utilisateur réel
est intact.

---

## Ce qui n'a pas été fait, et pourquoi

**Aucune restauration n'a été tentée.** La base est ignorée par Git
(`.gitignore` : `*.db`), non suivie, et aucune sauvegarde de l'état antérieur
n'existait. Reconstruire une base « d'avant » aurait signifié fabriquer un
fichier qui n'a jamais existé sous cette forme — un mensonge plus dangereux que
l'écart lui-même. Aucune table n'a été supprimée, aucune migration rejouée.

---

## Baselines

**Baseline historique, antérieure à la Phase 37** — conservée ici pour
l'historique, elle n'est plus la référence opérationnelle :

```
sha256 e7923400a09900fbefb83728d4e76ae8a88ae969b467753eee2134729304c153
size   212992
mtime  2026-08-19 00:17:16
```

**Baseline courante, à partir du 29 août 2026** :

```
sha256 86b449aa1e2d266a27b45eced73aa3e5ace0b0aa08180c3cfd25cd1d42bcf71f
size   401408
mtime  2026-08-29 00:20:15
```

Une copie immuable de cet état est conservée **hors dépôt**, dans
`C:\Users\sash\Documents\nova-server-backups\`, et n'est ni versionnée ni
transmise nulle part. Son empreinte est identique à celle de la source.

---

## Le garde-fou existant, et sa portée réelle

`recipe_env.py` refuse de démarrer si `NOVA_DB` n'est pas défini, ou s'il
désigne la base réelle, ou s'il pointe hors d'un répertoire temporaire.

Audit de couverture après incident — **tous les chemins officiels sont
protégés** :

- `scim_recipe.py` importe `recipe_env` avant `main`, et vérifie que la base
  résolue est bien la copie temporaire ;
- chaque fichier de test définit `NOVA_DB` avant d'importer `main`, soit
  directement, soit en important `test_main` — qui le fait — en premier ;
- `serve.py`, `admin_cli.py` et `local_channel.py` ouvrent la base réelle
  délibérément : ce sont les points d'entrée de l'application, pas des recettes.

Le seul chemin non protégé est l'invocation manuelle `python -c "import main"`,
c'est-à-dire précisément celle qui a causé l'incident. Le garde-fou n'a donc pas
échoué : il n'a pas été invoqué.

**Aucune modification n'a été apportée au garde-fou dans le cadre de cette
clôture.** Rendre `import main` inoffensif en toute circonstance — en repoussant
`init_db()` hors du temps d'import — est un changement d'architecture qui mérite
sa propre décision, pas un correctif glissé dans une clôture de phase.

En attendant : toute vérification ponctuelle passe par `NOVA_DB` pointant vers
une copie temporaire, ou par une ouverture SQLite en lecture seule.
