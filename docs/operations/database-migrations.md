# Base de données et migrations — guide d'exploitation

Ce que vous devez savoir pour faire tourner, migrer et diagnostiquer la base du
Control Plane.

L'architecture et les décisions sont dans
[`docs/architecture/production-hardening.md`](../architecture/production-hardening.md).

---

## 1. Quel moteur, où

| Environnement | Moteur | Variable |
| --- | --- | --- |
| Production | **PostgreSQL**, obligatoire | `NOVA_DATABASE_URL=postgresql://…` |
| Développement | SQLite | `NOVA_DB=nova.db` ou `NOVA_DATABASE_URL=sqlite:///…` |
| Tests, recettes | SQLite temporaire | idem |

En production, `NOVA_DATABASE_URL` est **exigée** : le service refuse de
démarrer sans elle plutôt que de se rabattre sur `NOVA_DB`, qui ferait passer un
oubli de configuration pour un choix. Une URL SQLite en production est refusée
avec `SQLITE_REFUSED_IN_PRODUCTION`.

SQLite n'a qu'un seul écrivain. La limite ne se manifeste pas par une lenteur,
mais par des écritures refusées au moment précis où le service est le plus
sollicité.

---

## 2. La commande

```
python -m migrations status     # état, sans aucun effet de bord
python -m migrations migrate    # applique ce qui manque
python -m migrations adopt      # déclare une base historique conforme
python -m migrations check      # code de sortie 1 si le schéma n'est pas à jour
```

`--url` remplace `NOVA_DATABASE_URL` pour un appel ponctuel.

### Une migration ne se déclenche jamais toute seule

C'est le changement central de cette phase. Le schéma était appliqué **à
l'import de `main`** : importer le module suffisait à migrer une base de
production. Ce n'est pas une hypothèse, c'est arrivé deux fois — Phase 30 puis
Phase 37.

Aujourd'hui, importer `main` ne crée rien, ne lit rien, ne décide rien. Un test
le vérifie en mesurant les tables avant et après l'import.

---

## 3. Démarrage du service

Le service **vérifie** le schéma, il ne le corrige pas.

| État de la base | Comportement |
| --- | --- |
| à jour | démarre |
| en retard | refuse : `DATABASE_MIGRATION_REQUIRED` |
| plus récente que le binaire | refuse : `DATABASE_SCHEMA_TOO_NEW` |
| non versionnée, schéma reconnu | refuse : lancer `adopt` |
| non versionnée, schéma non reconnu | refuse : voir `adoption_gaps` |

Une migration au démarrage se déclencherait au pire moment — sur le premier
processus qui redémarre, sans que personne ne l'ait demandée, et
potentiellement sur plusieurs workers à la fois.

Un schéma plus récent n'est **jamais** rétrogradé automatiquement.

---

## 4. Ordre de déploiement recommandé

1. `python -m migrations status` sur la base cible ;
2. sauvegarde ;
3. `python -m migrations migrate` ;
4. déployer le binaire ;
5. le démarrage vérifie et refuse si quelque chose ne correspond pas.

---

## 5. Versions et baseline

La table `schema_migrations` porte `version`, `name`, `applied_at`.

La **version 1** est l'état complet du schéma à la fin de la Phase 37, dans
`migrations/0001_baseline.sql`. Ce n'est pas un historique reconstitué : le
schéma n'en avait pas, et en inventer un après coup aurait produit des
migrations que personne n'a jamais exécutées dans cet ordre.

Ajouter une migration : déposer `migrations/NNNN_nom.sql`. L'ordre vient du
numéro, jamais de l'ordre du système de fichiers — deux machines listeraient un
répertoire différemment.

Chaque migration s'exécute dans **sa** transaction, avec l'écriture de sa
version. Un échec n'avance pas la version, ne laisse aucun état déclaré réussi,
et arrête la suite.

> Détail qui a nécessité une correction : le module `sqlite3` de Python n'ouvre
> pas de transaction avant du DDL — seulement avant un `INSERT`, `UPDATE` ou
> `DELETE`. Sans `BEGIN` explicite, un `CREATE TABLE` s'auto-validait, et une
> migration échouant à sa troisième instruction laissait les deux premières en
> place. Le moteur ouvre donc la transaction lui-même. PostgreSQL n'avait pas ce
> défaut.

---

## 6. Adopter une base existante

Une base déjà au schéma de la Phase 37 mais sans `schema_migrations` est
**adoptée** : on vérifie, puis on déclare. Rejouer la baseline sur une base qui
porte déjà les tables échouerait — ou, pire, réussirait à moitié.

L'adoption ne se décide jamais sur une seule colonne. Elle exige :

- les six tables du noyau : `organizations`, `users`, `tokens`,
  `federated_identities`, `admin_sessions`, `admin_audit_log` ;
- les colonnes portant le cloisonnement par organisation établi en Phase 36 —
  `users.user_id`, `users.organization_id`, `users.security_role`,
  `federated_identities.external_issuer`, entre autres.

Si quelque chose manque, l'adoption est **refusée** et `status` liste
précisément quoi, dans `adoption_gaps`. Une base historique SQLite plus
ancienne passe d'abord par `init_db()`, qui complète les tables et les colonnes
avant d'adopter.

---

## 7. Ce que PostgreSQL applique et que SQLite n'appliquait pas

Découvert en portant le schéma : les clés étrangères de `scim_user_management`
et `organization_group_memberships` visent `users(organization_id, user_id)`,
dont l'unicité vient d'un index créé séparément. PostgreSQL exige que cette
unicité existe **avant** la clé étrangère ; SQLite ne vérifiait rien, les clés
étrangères y étant désactivées par défaut.

La baseline est donc ordonnée topologiquement, chaque index unique suivant
immédiatement sa table. Conséquence à connaître : sous PostgreSQL, ces relations
sont **réellement** appliquées.

---

## 8. Panne de base

Le service ne se rabat sur rien. Une base injoignable produit une erreur ; il
n'existe aucun repli vers SQLite, en production comme ailleurs. Un repli
silencieux donnerait un service qui semble fonctionner tout en écrivant dans une
base que personne ne sauvegarde.

Le pool est borné (`NOVA_DB_POOL_MIN`, `NOVA_DB_POOL_MAX`,
`NOVA_DB_POOL_TIMEOUT`) : sous charge, il attend puis refuse, au lieu d'ouvrir
plus de connexions que PostgreSQL n'en accepte. Une connexion sur laquelle le
pilote a perdu pied ne retourne pas au pool.

---

## 9. Écrire du SQL qui marche sous les deux moteurs

Le SQL applicatif garde la syntaxe `?` : l'adaptateur traduit vers `%s` quand la
cible est PostgreSQL, sans jamais toucher au contenu d'un littéral.

Interdits dans le chemin applicatif, et refusés explicitement par l'adaptateur :

| Construction | À la place |
| --- | --- |
| `INSERT OR REPLACE` | `INSERT … ON CONFLICT(clé) DO UPDATE` |
| `AUTOINCREMENT` | laisser la baseline le rendre par dialecte |
| `PRAGMA` | `conn.columns(table)`, `conn.table_exists(table)` |
| `BEGIN IMMEDIATE` | `conn.begin_immediate()` |
| `lastrowid` | `INSERT … RETURNING id` |

`INSERT OR IGNORE` est traduit en `ON CONFLICT DO NOTHING` et peut rester tel
quel.

Attraper `database.IntegrityError`, jamais `sqlite3.IntegrityError` : un
conflit doit rester un 409 sous les deux moteurs, sans devenir un 500 au passage
en production.

---

## 10. Ce qui reste en dialecte SQLite, et pourquoi

`main.migrate_schema()` met à niveau une base **SQLite historique** :
reconstruction de tables et introspection `PRAGMA`, deux choses qui n'existent
que là. Une base PostgreSQL n'a pas d'historique à rattraper — elle naît au
dernier schéma. La fonction refuse donc explicitement de s'exécuter sous
PostgreSQL, avec `LEGACY_MIGRATION_SQLITE_ONLY`, plutôt que d'y produire une
erreur obscure.

C'est la seule exception, et elle n'est jamais atteinte en production.
