# Durcissement pour la production — architecture

Nova fonctionne. Ce document décrit ce qui manque pour qu'il fonctionne
*encore* quand il y a quatre workers, deux machines, une panne Redis, une
restauration à faire et personne pour surveiller.

Aucune fonctionnalité produit n'est ajoutée ici. Ce qui suit est de
l'infrastructure.

---

## 1. Audit de départ

L'audit a précédé le code. Il porte sur le service tel qu'il est à la fin de la
Phase 37.

| Sujet | Implémentation actuelle | Risque de production | Cible | Impact migration | Sévérité |
| --- | --- | --- | --- | --- | --- |
| Moteur de base | SQLite, fichier local, `sqlite3.connect` par requête | Un seul écrivain. Plusieurs workers sur plusieurs machines est impossible, pas seulement lent | PostgreSQL en production, SQLite en dev/test | Élevé — 580 sites d'appel | **P0** |
| Pool de connexions | Aucun. Une connexion ouverte et fermée par requête | Coût par appel, et rien ne borne le nombre de connexions sous charge | Pool borné, attente puis refus | Faible | **P1** |
| Transactions | `db()` valide à la sortie, annule sur exception | Correct, et à conserver tel quel | Inchangé | Nul | OK |
| Concurrence | `BEGIN IMMEDIATE` (3 sites) + contraintes uniques | `BEGIN IMMEDIATE` n'existe pas sous PostgreSQL ; ce sont les contraintes qui doivent trancher | Contraintes uniques comme seule autorité | Moyen | **P1** |
| Migrations | `init_db()` + `migrate_schema()` exécutées **à l'import de `main`** | Une migration de production se déclenche par un import. C'est la cause des incidents Phase 30 et Phase 37 | Commande explicite, table de version | Moyen | **P0** |
| Version de schéma | Aucune table de version | Impossible de savoir si un binaire et une base sont compatibles | `schema_migrations` | Faible | **P1** |
| Contraintes | Uniques et FK présentes, tenant-scoped depuis la Phase 36 | Correct | Inchangé, à revérifier sous PostgreSQL | Faible | OK |
| Limitation de débit | Deux compteurs en mémoire de processus (`main.py`, `scim.py`) | Derrière 4 workers, une limite de 300/min en autorise 1200. La protection est annoncée mais absente | Redis, fenêtre glissante atomique | Faible | **P0** |
| Secrets | Variables d'environnement, valeurs brutes | Un `.env` finit dans une image, une sauvegarde ou un dépôt | `env://` / `file://`, extensible | Faible | **P1** |
| Clés de signature | `NOVA_CONNECTOR_RELEASE_PUBLIC_KEY`, `NOVA_DESKTOP_RELEASE_PUBLIC_KEY` | Aucun contrôle de démarrage n'exige leur présence en production | Vérification au démarrage | Faible | **P1** |
| Rotation de credentials | Connector : manuelle (Phase 34) | Un credential qui ne tourne jamais finit par fuiter | Planification, avec la machine à états existante | Moyen | **P2** |
| Rotation de racine de confiance | Aucune | Changer la clé de release invaliderait tout le parc d'un coup | Clé courante + suivante, recouvrement | Moyen | **P2** |
| Santé | Aucun `/health`, `/readyz` | Un orchestrateur ne sait pas si le processus peut servir | Liveness et readiness séparés | Faible | **P1** |
| Métriques | Aucune | Aucune visibilité opérationnelle | Prometheus | Faible | **P1** |
| Journaux | `print()` non structuré | Illisible en agrégation, et rien ne garantit l'absence de contenu | Journaux structurés, `request_id` | Faible | **P1** |
| Sauvegarde/restauration | Aucune procédure | Perte de données irrécupérable | `pg_dump`, restauration vers base temporaire, exercice | Faible | **P0** |
| Tâches périodiques | Aucune | — | Verrou consultatif si ajoutées | Faible | **P2** |
| En-têtes de sécurité | CORS configuré ; HSTS/CSP/`X-Frame-Options` absents | Console d'administration exposée | Ajoutés en production | Faible | **P1** |
| Proxy de confiance | Aucun traitement de `X-Forwarded-For` | Une IP usurpée contournerait une limite par IP | Confiance restreinte au proxy déclaré | Faible | **P1** |
| Authentification héritée | `X-Admin-Token` et le jeton de déploiement retirés du code (Phases 33/36) | Aucun | Inchangé | Nul | OK |
| `AdminPrincipal.is_legacy_token` | Présent, `main.py:3283` | Code mort qui suggère un chemin d'authentification disparu | Suppression après preuve | Nul | **P2** |

---

## 2. Décision — PostgreSQL en production, SQLite en développement

SQLite a bien servi et continue de servir : un test qui démarre en trois
millisecondes se lance cent fois par jour ; un test qui demande un conteneur se
lance beaucoup moins. Il reste donc le moteur du développement, des tests et des
recettes.

Ce qu'il ne fera jamais, c'est plusieurs processus qui écrivent depuis plusieurs
machines. Un plan de contrôle centralisé finit toujours par en avoir besoin.

### Ce qui a été écarté

- **Un ORM.** Le Control Plane écrit du SQL revu, dont on peut lire le plan
  d'exécution. Une couche d'objets raccourcirait les requêtes et rendrait le
  comportement moins prévisible.
- **Réécrire les 580 appels à la main.** Même sémantique, 580 occasions de se
  tromper. L'adaptateur traduit la syntaxe `?` vers `%s` à l'exécution.
- **Un limiteur « distribué » bâti sur SQLite.** Compter dans la base que l'on
  protège invite le déni de service à passer par le compteur.

---

## 3. La couche d'accès — `database.py`

Un contrat, deux moteurs. Ce qu'elle garantit :

- **Une exception d'intégrité unique.** Le code applicatif attrape
  `database.IntegrityError` et non `sqlite3.IntegrityError` : une contrainte
  unique doit produire le même 409 sous les deux moteurs, sinon le passage en
  production changerait silencieusement un conflit en erreur 500.
- **Des lignes indexables par nom et par position**, comme `sqlite3.Row`, parce
  que le code existant fait les deux.
- **`?` traduit sans toucher aux littéraux.** Un `?` dans un message stocké
  n'est pas un paramètre.
- **Tous les `%` doublés, littéraux compris.** C'est le piège que le test a
  attrapé : psycopg analyse les `%` sur la requête entière, et un
  `LIKE '%match'` non doublé est lu comme le marqueur `%m` puis rejeté.
- **Les constructions propres à SQLite refusées, jamais transmises.**
  `INSERT OR REPLACE`, `AUTOINCREMENT`, `PRAGMA` et `BEGIN IMMEDIATE` lèvent
  une erreur explicite plutôt que d'arriver telles quelles à un moteur qui les
  comprendrait de travers. `INSERT OR IGNORE` a un équivalent exact et sûr
  (`ON CONFLICT DO NOTHING`), il est traduit.
- **Un pool borné.** Sans borne, un pic de trafic ouvre plus de connexions que
  PostgreSQL n'en accepte, et le service tombe au moment où il est le plus
  sollicité.
- **`begin_immediate()` qui ne simule rien.** Sous SQLite il prend le verrou
  d'écriture ; sous PostgreSQL il ne fait rien, parce que la transaction est
  déjà ouverte et que ce sont les contraintes uniques qui tranchent.

Vérifiée par 38 tests exécutés contre **les deux moteurs réels**, dont une
course à quatre threads qui doit produire exactement un gagnant sous l'un comme
sous l'autre.

---

## 4. Le limiteur — `ratelimit.py`

Le défaut corrigé est précis : deux limiteurs comptaient dans un dictionnaire en
mémoire de processus. Derrière quatre workers, une limite annoncée à 300 appels
par minute en autorisait mille deux cents, et rien ne le disait. **Une limite
qu'on croit avoir est pire qu'une limite qu'on sait ne pas avoir.**

Le remplacement est un compteur Redis à fenêtre glissante, implémenté en Lua :
purge, lecture et écriture en un seul aller-retour atomique. Un `GET` suivi d'un
`SET` depuis quatre processus laisserait passer exactement ce que le limiteur
est censé empêcher.

### Que faire quand Redis tombe

La politique est déclarée par quota, jamais par défaut :

| Quota | Limite | Redis absent |
| --- | --- | --- |
| SCIM par credential | 300/min | refuse |
| SCIM par tenant | 900/min | refuse |
| Enrôlement Connector | 5/min | refuse |
| Mise à jour Connector | 30/min | refuse |
| Démarrage d'authentification | 10/min | refuse |
| Vérification d'authentification | 20/min | refuse |
| Step-up administrateur | 10/min | refuse |
| Création de credential | 20/min | refuse |
| Vérification de déploiement | 60/min | refuse |
| **Synchronisation Connector** | 120/min | **laisse passer** |

Une seule exception, et elle est motivée : refuser toutes les synchronisations
pendant une panne Redis couperait la configuration de chaque organisation.
Compter faux quelques minutes coûte moins cher que tout arrêter. Tout chemin
d'authentification, lui, refuse — sans compteur, on ne distingue pas un
utilisateur d'un attaquant qui essaie mille fois.

Les valeurs reprennent exactement celles déjà appliquées : on change la
mécanique, pas la politique. Un seul changement à la fois.

En développement, sans Redis, un compteur local prend le relais — et
`limiter.shared` vaut `False`, pour que rien ne puisse présenter cette
protection comme partagée.

Vérifié par 16 tests, dont **deux processus Python distincts** partageant un
quota de 6 : cinq tentatives chacun, six autorisations au total. Avec des
compteurs séparés, chacun en aurait autorisé cinq.

---

## 5. Les secrets — `secrets_provider.py`

Une **référence** plutôt qu'une valeur :

```
env://NOVA_MICROSOFT_CLIENT_SECRET
file:///run/secrets/nova_microsoft_client_secret
```

La valeur brute reste acceptée pour ne pas casser les déploiements existants,
mais elle est identifiée comme telle : en production, un secret en clair dans
l'environnement devient un choix visible, pas un défaut.

Pour `file://` : chemin absolu obligatoire (un chemin relatif dépendrait du
répertoire de lancement), taille bornée, UTF-8 vérifié, fin de ligne retirée —
`echo secret > fichier` en ajoute une, et la garder produirait un échec
d'authentification indiagnosticable —, et refus d'un fichier lisible par tout le
monde là où le système sait le dire.

Un schéma inconnu est **refusé**, jamais pris pour une valeur littérale : sans
cela, une faute de frappe dans `env://` deviendrait silencieusement un secret
valant « env:/NOVA_… ».

La valeur ne sort ni du `repr` ni du `str` : un secret interpolé par mégarde
dans un message ne fuit pas. `describe()` donne la provenance sans la valeur.

Aucun gestionnaire externe n'est intégré. Vault, AWS, Azure et GCP sont trois
intégrations à maintenir ; en écrire trois maintenant produirait trois chemins
peu testés. La forme `schéma://référence` existe pour qu'un quatrième s'ajoute
sans toucher aux points d'appel.

Vérifié par 22 tests, dont l'absence de fuite dans les représentations.

---

## 6. État de livraison

Cette phase est **partielle**, et il vaut mieux l'écrire que le laisser
découvrir.

### Livré et vérifié contre l'infrastructure réelle

| Élément | Preuve |
| --- | --- |
| Couche d'accès deux moteurs | 38 tests, SQLite **et** PostgreSQL 16 réels |
| Pool borné, refus plutôt que croissance | testé contre PostgreSQL réel |
| Base injoignable → échec, jamais de repli | testé |
| Course concurrente → un seul gagnant | testé sur les deux moteurs |
| Limiteur distribué Redis | 16 tests, Redis 7 réel |
| Partage entre processus | deux processus distincts, égalité stricte |
| Politique fail-closed / fail-open | testée quota par quota |
| Résolution de secrets `env://` / `file://` | 22 tests |
| Absence de fuite de secret | testée |

### Livré par la continuation A — portage effectif

| Élément | Preuve |
| --- | --- |
| Migrations versionnées, `schema_migrations` | 21 tests, SQLite et PostgreSQL |
| Commande `python -m migrations` | status / migrate / adopt / check |
| Import de `main` sans effet de bord | test en sous-processus : 0 table créée |
| `main.py` sur la couche d'accès | 0 `sqlite3.*` dans le chemin runtime |
| Schéma dupliqué supprimé | `_legacy_sqlite_bootstrap` (397 lignes) retiré |
| Control Plane sous PostgreSQL | 8 scénarios HTTP réels en sous-processus |
| Adoption d'une base historique | testée, avec refus motivé si invariant absent |
| Migration cassée | rollback vérifié sous les deux moteurs |
| Production refuse SQLite | 4 tests |

Deux défauts réels trouvés par ces tests et corrigés :

1. **`executescript` de SQLite validait avant de commencer.** Une migration qui
   échouait à sa troisième instruction laissait les deux premières en place. Le
   moteur ouvre désormais la transaction lui-même — le module `sqlite3` n'en
   ouvre pas avant du DDL. PostgreSQL n'avait pas ce défaut.
2. **Les clés étrangères n'étaient pas appliquées sous SQLite.**
   `scim_user_management` et `organization_group_memberships` visent
   `users(organization_id, user_id)`, dont l'unicité vient d'un index séparé.
   PostgreSQL l'exige avant la clé étrangère et refusait le schéma. La baseline
   est donc ordonnée topologiquement — et ces relations sont désormais
   réellement appliquées.

### Livré par la continuation B — sécurité runtime et exploitation

| Élément | Preuve |
| --- | --- |
| Limiteur branché sur les routes | les deux compteurs process-local retirés |
| Quota partagé sur une **vraie route HTTP** | 2 processus, quota total = quota, pas son double |
| Panne Redis : fail-closed / fail-open | testé quota par quota, reprise sans redémarrage |
| Secrets par référence | Google, OIDC, SMTP, jeton runtime IA |
| Validation de configuration production | 25 tests, tous les problèmes collectés d'un coup |
| `/api/livez` indépendant de la base | testé base coupée |
| `/api/readyz` sans détail d'infrastructure | testé |
| Métriques Prometheus | aucune étiquette non bornée, testé |
| Journaux structurés + `request_id` | champs sensibles caviardés, sentinelles |
| En-têtes de sécurité, proxy de confiance | testés sur de vraies réponses |
| Sauvegarde / restauration PostgreSQL | exercice complet sur PostgreSQL 16 réel |
| Course du dernier administrateur | **défaut réel corrigé**, voir ci-dessous |
| `AdminPrincipal.is_legacy_token` | supprimé, aucun appelant |

Un défaut réel trouvé et corrigé : **la garde du dernier gestionnaire
d'identités ne protégeait rien sous concurrence.** Elle lisait la liste des
administrateurs puis comparait ; deux transactions retirant chacune un des deux
derniers lisaient toutes deux « il en reste deux » et concluaient toutes deux
que ce n'était pas le dernier. La garde verrouille désormais les comptes
d'administration avant de compter — `SELECT … FOR UPDATE` sous PostgreSQL,
verrou d'écriture immédiat sous SQLite.

### Non livré

Le portage effectif de `main.py` vers la couche d'accès n'est **pas** fait. Le
service continue d'ouvrir SQLite directement. La couche existe, elle est
testée, et rien ne l'utilise encore.

C'est délibéré : brancher 580 sites d'appel et réécrire `init_db` /
`migrate_schema` en DDL bi-dialecte est le cœur du travail restant, et le
livrer à moitié — un Control Plane qui marche sous SQLite et casse sous
PostgreSQL en production — serait pire que de ne pas l'avoir commencé.

Restent également non livrés : le cadre de migrations versionnées, l'outil
SQLite → PostgreSQL, la sauvegarde/restauration, les points de santé, les
métriques, les journaux structurés, les contrôles de démarrage, les rotations
automatiques, les en-têtes de sécurité et le pipeline de publication.

Le détail et l'ordre recommandé figurent dans le rapport de phase.
