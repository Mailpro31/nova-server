# Runbook de production

Ce qu'il faut pour faire tourner le Control Plane, et quoi faire quand quelque
chose casse.

Compléments : [architecture](../architecture/production-hardening.md),
[base et migrations](./database-migrations.md).

---

## 1. Environnement requis

| Variable | Rôle | Production |
| --- | --- | --- |
| `NOVA_ENV` | `development` / `test` / `production` | obligatoire, jamais deviné |
| `NOVA_DATABASE_URL` | `postgresql://…` | **obligatoire** |
| `NOVA_REDIS_URL` | compteur partagé | **obligatoire** |
| `NOVA_ADMIN_ORIGINS` | allowlist de la console | HTTPS publiques |
| `NOVA_SERVICE_ENDPOINT` | origine annoncée aux postes | HTTPS publique |
| `NOVA_TRUSTED_PROXIES` | adresses des reverse proxies | vide par défaut |
| `NOVA_DB_POOL_MIN/MAX/TIMEOUT` | bornes du pool | défauts 1 / 10 / 10 s |

`NOVA_ENV` vaut `production` par défaut : un déploiement qui oublie de se
déclarer obtient le comportement le plus prudent, jamais le plus bavard.

### Secrets

Une **référence** plutôt qu'une valeur, partout où un secret est attendu :

```
NOVA_GOOGLE_CLIENT_SECRET=file:///run/secrets/google_client_secret
NOVA_OIDC_CLIENT_SECRET=env://OIDC_SECRET_FROM_VAULT
NOVA_SMTP_PASS=file:///run/secrets/smtp
```

La valeur brute reste acceptée — c'est ce qui permet à Kubernetes ou Docker
d'injecter par l'environnement — mais le démarrage refuse une valeur faible,
trop courte, ou par défaut. Un fichier de secret doit être en chemin absolu,
UTF-8, de taille bornée, et non lisible par tout le monde.

---

## 2. Démarrage

```
python -m migrations status          # état, sans effet de bord
python -m migrations migrate         # migration explicite
uvicorn main:app --host 127.0.0.1 --port 8000
```

Le service **ne migre jamais tout seul**. Il refuse de démarrer sur une base en
retard (`DATABASE_MIGRATION_REQUIRED`) ou plus récente que lui
(`DATABASE_SCHEMA_TOO_NEW`).

La validation de configuration s'appelle explicitement :

```python
production_config.enforce()
```

Elle collecte **tous** les problèmes avant de refuser : un opérateur qui corrige
veut la liste, pas une découverte à chaque redémarrage.

Ce qui est refusé en production : SQLite, `NOVA_DATABASE_URL` absente, Redis
absent, origine publique en clair ou locale, secret faible ou irrésolvable,
drapeau `*_ALLOW_INSECURE`, variable d'authentification héritée encore présente.

---

## 3. Sondes

| Route | Répond | Dépend de |
| --- | --- | --- |
| `/api/livez` | le processus vit | rien |
| `/api/readyz` | le service peut servir | base, schéma, compteur partagé |
| `/api/health` | contrat historique du poste | base |
| `/api/metrics` | exposition Prometheus | — |

**Ne branchez pas la sonde de vivacité sur `/api/health`.** Elle lit la base :
un orchestrateur redémarrerait un processus parfaitement vivant parce que sa
base est momentanément injoignable. `/api/livez` existe pour cela.

`/api/readyz` renvoie 503 quand un composant manque, et son corps ne contient ni
URL, ni nom d'hôte, ni chemin de secret — c'est souvent la première chose qu'on
expose sans authentification.

---

## 4. Métriques et journaux

Métriques : nombre de requêtes, latence, erreurs serveur, état du pool,
compteur partagé actif ou non, événements d'exploitation.

**Aucune étiquette ne porte d'identifiant.** Ni utilisateur, ni organisation, ni
parc, ni connecteur, ni chemin brut : `/api/control/organizations/{id}/members`
devient la classe `control`. Une métrique étiquetée par identifiant fabrique une
série par identifiant — et publie la liste au passage.

Journaux : une ligne JSON par événement, avec `request_id`. Les champs nommés
`token`, `password`, `transcript`, `prompt`, `email` et leurs voisins sont
caviardés : l'opérateur voit qu'il y avait quelque chose, sans que la chose
sorte.

`X-Request-ID` est accepté du client s'il est bien formé, sinon régénéré. **Il
n'accorde aucune confiance** : il ne sélectionne aucun tenant et n'authentifie
personne.

Le journal d'audit métier (`admin_audit_log`) reste distinct. Les journaux
techniques tournent et se purgent ; l'audit doit rester.

---

## 5. Limitation de débit

Un compteur Redis partagé par tous les workers, en fenêtre glissante atomique.

Quotas fail-closed : SCIM 300/min par credential et 900/min par tenant,
enrôlement Connector 5/min, mise à jour Connector 30/min, demande
d'authentification 10/min, vérification 20/min, step-up 10/min, création de
credential 20/min, vérification de déploiement 60/min.

Un seul quota fail-open : **synchronisation Connector**, 120/min. Refuser toutes
les synchronisations pendant une panne Redis couperait la configuration de
chaque organisation ; compter faux quelques minutes coûte moins cher.

### Panne de Redis

- routes d'authentification et SCIM : refusent, avec `503`
  `RATE_LIMIT_BACKEND_UNAVAILABLE` ;
- synchronisation Connector : continue ;
- `/api/readyz` passe à 503 en production ;
- au retour de Redis, le compteur repart **sans redémarrage**.

Aucun repli vers un compteur local en production. Sans compteur partagé, rien ne
distingue un utilisateur d'un attaquant qui essaie mille fois.

---

## 6. Sauvegarde

```
python -m backup dump /srv/backups/nova-$(date +%F).dump
python -m backup verify /srv/backups/nova-2026-08-29.dump
```

Un manifeste est écrit à côté : empreinte SHA-256, taille, version de schéma,
version du serveur. Il permet de vérifier une archive **sans base** — c'est la
question qu'on se pose à trois heures du matin.

Aucun identifiant de connexion n'entre dans le manifeste : le mot de passe vient
de `PGPASSWORD`, de `.pgpass` ou de l'URL, et n'est écrit nulle part.

`NOVA_PG_COMMAND_PREFIX` permet d'exécuter `pg_dump` ailleurs — un conteneur
voisin, un sidecar — le dump transitant par la sortie standard.

**Cadence et rétention recommandées**, non garanties : une sauvegarde complète
quotidienne, conservée trente jours, plus une copie hors site hebdomadaire. Le
chiffrement au repos relève du stockage, pas de Nova : n'attendez pas de ce
module qu'il chiffre l'archive.

Ce que contient une sauvegarde : identités, configuration, métadonnées de
contrôle. Ce qu'elle ne contient pas : audio, transcriptions, prompts,
presse-papiers, historique de dictée — cela n'a jamais quitté l'infrastructure
de l'organisation. Elle reste néanmoins une donnée personnelle : adresses
e-mail et empreintes de session.

### RPO / RTO

Recommandations, pas engagements. Le RPO découle de la cadence choisie — une
sauvegarde quotidienne accepte jusqu'à vingt-quatre heures de perte. Le RTO
dépend de l'infrastructure de restauration, pas de Nova.

---

## 7. Restauration

```
python -m backup restore /srv/backups/nova-2026-08-29.dump \
  --url postgresql://…/nova_restored
```

**La cible doit être une base neuve.** Restaurer par-dessus une base peuplée est
refusé (`RESTORE_TARGET_NOT_EMPTY`) sauf `--allow-non-empty` explicite : une
commande qui écrase la production sans rien demander finit toujours par être
lancée sur la mauvaise base.

Après restauration, vérifier : `python -m migrations status` doit dire
`current` ; les effectifs par table doivent correspondre au manifeste ; aucune
identité fédérée orpheline ; les contraintes d'unicité rejouent bien.

L'exercice complet — peupler, sauvegarder, détruire, restaurer ailleurs,
vérifier — est automatisé dans `test_operations.py`.

---

## 8. Incidents

### Base indisponible

Le service échoue proprement. **Aucun repli vers SQLite** : un repli silencieux
donnerait un service qui semble fonctionner tout en écrivant dans une base que
personne ne sauvegarde. `/api/readyz` passe à 503 ; `/api/livez` reste 200, donc
l'orchestrateur ne redémarre pas inutilement. Au retour de la base, le pool
reprend sans redémarrage ; une connexion dont le pilote a perdu pied n'y
retourne pas.

### Redis indisponible

Voir §5. Redis n'est **jamais** source de vérité : il ne porte que des
compteurs, des verrous et du cache court. Le vider ne coûte rien d'autre que des
compteurs — c'est vérifié par un test.

### Processus du Control Plane perdu

Sans état en mémoire à préserver : redémarrer. Les sessions vivent en base, les
compteurs dans Redis.

### Connector déconnecté

Le Connector continue de servir la configuration qu'il a déjà appliquée. Le
Data Plane de l'organisation ne dépend pas de la disponibilité du Control Plane
pour la dictée.

---

## 9. Rotation

- **Secret de fournisseur** : mettre à jour la référence (`file://` ou `env://`)
  puis redémarrer. Le jeton du runtime IA est résolu à chaque appel, donc une
  rotation de fichier monté y prend effet sans redémarrage.
- **Credential Connector** : rotation manuelle depuis Nova Admin, machine à
  états de la Phase 34 — l'ancien reste valide jusqu'à ce que le nouveau soit
  prouvé. La planification automatique n'existe pas ; voir P2.
- **Racine de confiance de release** : pas de rotation outillée. Voir P2.

---

## 10. TLS et proxy

TLS est **obligatoire** en production et peut terminer au reverse proxy ;
l'application ne gère pas de certificat. Un `bind` sur `127.0.0.1` derrière un
proxy est normal — c'est l'origine *annoncée* qui doit être publique et en
HTTPS, pas l'adresse d'écoute.

`X-Forwarded-For` n'est lu que si la connexion vient d'une adresse listée dans
`NOVA_TRUSTED_PROXIES`, et c'est le **dernier** élément qui est retenu — celui
que ce proxy a ajouté, non le premier, que le client contrôle. Vide par défaut :
aucun proxy n'est cru tant qu'on ne l'a pas déclaré.

CORS : allowlist explicite, `allow_credentials` faux. La console porte son jeton
dans un en-tête, jamais dans un cookie ambiant — il n'y a donc aucun cookie de
session à protéger, et aucun CSRF à défendre sur ce chemin.
