# Private Connector

## Le problème

Le plan de contrôle Nova vit chez Nova. Le runtime IA d'une organisation vit
chez l'organisation, sur un réseau privé. Pour qu'un administrateur puisse
changer un profil de modèle depuis Nova Admin, il faut que la décision traverse
cette frontière.

La façon évidente serait que Nova appelle l'infrastructure privée. Elle
demanderait à chaque établissement d'ouvrir un port entrant vers Internet et de
publier une adresse interne. Personne ne devrait avoir à faire cela pour
changer un réglage.

Le Connector inverse le sens de la connexion : c'est lui, depuis l'organisation,
qui va chercher ce qu'il doit appliquer.

```
NOVA CLOUD / CONTROL PLANE
        ▲
        │  HTTPS sortant, initié par l'organisation
        │
PRIVATE CONNECTOR  ── dans l'infrastructure de l'organisation
        │
        ▼
nova-server (plan de données)
        ├── runtime IA privé
        ├── policies / packages
        └── état de déploiement
```

## Frontières de confiance

| | Plan de contrôle Nova | Connector | Plan de données |
|---|---|---|---|
| Identité, organisations, rôles | ✅ autorité | — | consomme |
| Policies, packages | ✅ autorité | — | consomme |
| Configuration souhaitée du runtime | ✅ autorité | transporte | applique |
| Audio, prompts, transcriptions, sorties de modèle | ❌ jamais | ❌ jamais | ✅ seul |
| URL du runtime, nom de modèle, secrets d'inférence | ❌ jamais | ❌ jamais | ✅ seul |

Le Connector ne voit aucun contenu utilisateur, et n'a aucune raison d'en
demander. Le plan de contrôle n'en reçoit pas davantage après la Phase 33
qu'avant.

## Ce qui circule

**Autorisé** — identifiants techniques, révisions, statuts, versions de
protocole et de logiciel, résumé de santé, résumé matériel, profil sélectionné,
booléens de policy, codes d'erreur sûrs.

**Interdit** — audio, transcription, texte saisi ou sélectionné, prompt, sortie
de modèle, historique utilisateur, presse-papiers, contenu complet d'un Style,
instruction complète d'un AI Skill, credential, URL privée.

## Enrôlement

1. Un administrateur porteur de `deployment_manage` crée un code d'enrôlement
   pour **son** organisation. Le code est affiché une seule fois ; le serveur
   n'en garde que l'empreinte SHA-256.
2. Le Connector présente ce code à `POST /api/connector/enroll`.
3. Le plan de contrôle crée l'identité du Connector, la lie à l'organisation
   **portée par le code**, et délivre un credential durable — affiché une seule
   fois lui aussi, stocké en empreinte seule.
4. Le code d'enrôlement devient inutilisable.

L'organisation ne vient jamais du corps d'une requête de Connector. C'est ce qui
empêche un Connector de se déclarer chez quelqu'un d'autre.

Un code inconnu, déjà consommé ou expiré produit la même réponse
(`CONNECTOR_ENROLLMENT_INVALID`) : distinguer les trois cas dirait à qui sonde
lequel de ses essais a presque abouti.

## Cycle de vie du credential

- Haute entropie (`secrets.token_urlsafe(40)`), opaque, sans structure à
  interpréter.
- Stocké côté serveur en empreinte seule. Une copie de la base ne donne aucun
  credential utilisable.
- Côté Connector : variable d'environnement injectée par l'orchestrateur en
  priorité, sinon fichier d'état créé en `0600` **avant** écriture.
- Révocable à tout instant. Un credential révoqué et un credential inconnu
  donnent la même réponse.
- Ré-enrôlement : nouvelle identité, nouveau credential. L'ancien ne redevient
  jamais valide.

**mTLS** — envisageable plus tard sans changer le modèle : l'identité du
Connector est déjà une ligne en base, indépendante du moyen de la prouver.

## Rotation du credential

Un credential qui ne tourne jamais finit par être le plus vieux secret du
déploiement. La rotation le remplace **sans ré-enrôlement** : l'identité du
Connector, sa configuration et son historique restent les mêmes.

### La machine à états

```
stable ──(admin demande)──▶ requested ──(le Connector demande)──▶ pending
   ▲                                                                │
   └──────────────(le Connector prouve le nouveau)──────────────────┘
                                    │
                            (TTL dépassé)
                                    ▼
                                 failed
```

### L'ordre, et pourquoi il est celui-là

```
demander → persister → prouver → l'ancien meurt
```

Le plan de contrôle émet le nouveau secret **sans invalider l'ancien**. Le
Connector le persiste, puis appelle la confirmation **authentifiée avec le
nouveau** : le serveur ne croit pas sur parole qu'un secret a été reçu, il
constate que le Connector sait s'en servir. Seulement alors l'ancien cesse de
valoir.

Révéler le nouveau et tuer l'ancien dans le même geste transformerait la moindre
écriture disque ratée en ré-enrôlement manuel.

### Fenêtre de chevauchement

Bornée à `NOVA_CONNECTOR_ROTATION_TTL_SECONDS` (600 s par défaut). Pendant cette
fenêtre, l'ancien **et** le nouveau authentifient. Deux credentials durables
actifs indéfiniment doubleraient la surface au lieu de la faire tourner.

À l'expiration : le secret en attente est effacé, l'état passe à `failed`, et
**le credential courant reste valide**. Échouer dans l'autre sens enfermerait
dehors un Connector qui n'a rien fait de mal.

### Reprise après crash

L'état local est écrit atomiquement — fichier temporaire, `fsync`, remplacement
— et le secret en attente est persisté *avant* la confirmation. Si le processus
meurt entre les deux, le redémarrage retrouve le secret en attente et termine la
bascule. Aucun ré-enrôlement, aucune édition manuelle de fichier.

### L'administrateur ne voit jamais le secret

La console pose un drapeau (`rotation_state = requested`) ; le Connector conduit
la bascule et reçoit le secret directement. Faire transiter un credential
durable par un navigateur d'administration en ferait la pièce la plus exposée de
la chaîne.

### Rotation n'est pas révocation

La rotation suppose que l'ancien credential fonctionne encore assez longtemps
pour poser le nouveau. Un credential **compromis** se révoque : l'ancien meurt
immédiatement et le Connector doit être ré-enrôlé. Transformer un cas compromis
en rotation silencieuse laisserait l'attaquant conduire la bascule.

### Compatibilité

La capacité `credential_rotation_v1` est négociée. Un Connector antérieur ne la
déclare pas ; le plan de contrôle refuse alors la demande
(`CONNECTOR_ROTATION_UNSUPPORTED`) et la console propose de le mettre à jour ou
de le ré-enrôler, plutôt que d'émettre un ordre dans le vide.

### Ce qui n'est pas fait

La rotation **périodique automatique** n'est pas activée. Le modèle la permet —
l'âge du credential est exposé — mais l'activer demande une politique explicite :
quelle fréquence, et quel comportement si un Connector est hors ligne le jour
dit.

## Protocole

`connector_protocol_version = 1`, indépendant de la version marketing de Nova.
Un protocole non supporté est refusé proprement par `426
CONNECTOR_PROTOCOL_UNSUPPORTED` — jamais par une dégradation silencieuse.

**Connector → plan de contrôle** (`POST /api/connector/sync`)

```
protocol_version, connector_version, server_version, capabilities,
applied_revision, apply_status, last_error_code, health, local_time
```

**Plan de contrôle → Connector**

```
protocol_version, connector_id, heartbeat_seconds, server_time,
desired.ai_runtime, supported_operations
```

Rapport et battement de cœur sont le même appel. Les séparer donnerait deux
rythmes à accorder pour aucune information supplémentaire.

Le parsing est strict : un champ inconnu est refusé, pas ignoré. L'ignorer
ferait croire à un futur Connector que le plan de contrôle a compris une
instruction qu'il n'a jamais lue.

## Opérations, et rien d'autre

Le protocole ne connaît que des opérations nommées :

- `apply_ai_runtime_config`
- `report_health`

Il n'existe **aucune** commande générique à exécuter, aucun transfert de
fichiers, aucun tunnel. Le Connector n'est ni un VPN, ni un shell distant.

## Négociation de capacités

Le Connector déclare ce qu'il sait faire (`ai_runtime_config_v1`,
`health_reporting_v1`, `diagnostics_v1`). Le plan de contrôle ne pousse que ce
qui figure dans cette liste, et ignore toute capacité qu'il ne connaît pas.
Déduire une capacité d'un numéro de version marcherait jusqu'à la première
version intermédiaire.

## Souhaité → livré → appliqué

Trois faits distincts, jamais fondus :

| État | Qui l'établit | Signification |
|---|---|---|
| `desired` | l'administrateur | la révision est enregistrée |
| `delivered` | le plan de contrôle | la révision a été tendue à un Connector |
| `applied` | le Connector | le plan de données l'a confirmée |
| `failed` | le Connector | l'application a échoué, avec un code sûr |

`applied_revision` n'avance jamais toute seule. Un « Applied » affiché parce
qu'une configuration a été téléchargée enverrait chercher une panne dans le
modèle alors que rien n'a été posé.

**Idempotence** — la livraison est au-moins-une-fois. Une révision déjà
appliquée est acquittée sans être réappliquée.

**Ordre** — une révision antérieure à celle déjà appliquée est ignorée. Un
retour en arrière ne se fait pas en livrant une révision plus ancienne : le plan
de contrôle en crée une nouvelle portant l'ancienne configuration, exactement
comme un rollback de package.

**Reprise après crash** — l'état local est persisté *avant* l'acquittement. Si
le processus meurt entre l'application et l'ACK, le redémarrage retrouve la
révision posée et l'acquitte sans la réappliquer.

## Le canal local

### Le défaut corrigé

La Phase 33 faisait parler le Connector au plan de données par le **listener
public**, avec un **secret statique partagé** posé dans l'environnement et dans
le fichier compose. Ce secret n'avait ni rotation, ni révocation, ni durée de
vie ; il était le même à chaque redémarrage et donnait accès à la
reconfiguration de tout le runtime.

Deux choses changent, et elles sont indépendantes :

1. **La surface interne quitte le listener public.** Elle est servie par une
   application ASGI distincte (`local_channel.internal_app`), sur son propre
   point d'écoute. Une session Desktop ou d'administration arrivant sur l'API
   publique ne peut pas l'atteindre — non pas parce qu'un garde la refuse, mais
   parce qu'elle n'y est pas.
2. **Le secret statique disparaît du chemin nominal.**

### Transports

| Mode | Cible | Authentification |
|---|---|---|
| `unix` | production Linux / Docker | permissions du socket (`0660`) |
| `loopback` | développement, plateformes sans AF_UNIX | jeton **éphémère** régénéré à chaque démarrage, `127.0.0.1` uniquement |

Le choix est explicite (`NOVA_CONNECTOR_LOCAL_TRANSPORT`), jamais deviné. Une
détection automatique choisirait un jour le transport le plus faible sans que
personne ne l'ait décidé.

**Fail-closed** : `unix` sur une plateforme sans AF_UNIX échoue au démarrage avec
`LOCAL_TRANSPORT_UNAVAILABLE`. Il n'existe aucun repli silencieux vers la boucle
locale, et aucun secret par défaut sur lequel retomber.

Sur socket Unix, aucun jeton n'est envoyé : qui peut ouvrir le socket pourrait
aussi lire le fichier qui porterait le jeton. En ajouter un ne protégerait de
rien de plus et réintroduirait exactement le secret que cette phase retire.

### Opérations

Quatre, nommées : `get_local_capabilities`, `get_local_runtime_state`,
`apply_ai_runtime_config`, `report_local_health`. Pas de shell, pas d'URL
arbitraire, pas de SQL, pas d'accès au système de fichiers, pas de proxy.

Séquence d'application : valider, appliquer, vérifier la santé, puis répondre.
Une configuration refusée laisse l'ancienne active.

### Limites honnêtes

- **Windows** : `chmod` n'y bascule que le bit lecture seule. Les permissions
  POSIX du fichier de jeton et de l'état local n'y sont **pas** effectives, et
  AF_UNIX n'y est pas disponible. Le durcissement Windows — ACL, service dédié,
  named pipe — appartient à une phase ultérieure ; la parité n'est pas
  revendiquée.
- **Root local** : rien ici ne protège contre un administrateur de la machine
  compromis. Ce qui est visé : l'exposition réseau, l'accès accidentel par un
  autre processus, un secret réutilisable d'un déploiement à l'autre, et le
  rayon d'impact d'une fuite.
- **Fenêtre de permission du socket** : fermée, et vérifiée. uvicorn crée le
  socket avec le masque du processus, puis le passe explicitement à `0o666` —
  world-writable. Mesuré en conteneur : la séquence réelle est
  `0o755 → 0o666`. Un `chmod` après coup ne fait que raccourcir cette fenêtre.
  `serve.py` sécurise donc le **répertoire** (`0o770`) avant que le socket
  existe : sans permission de traversée, le mode du socket n'est plus une
  porte. Vérifié en conteneur : un utilisateur non privilégié obtient
  `Permission denied` en tentant de s'y connecter.

### Chemin hérité : **supprimé**

La route publique `/api/deployment/ai-runtime/apply` et le secret statique qui
la protégeait n'existent plus. Ni la route, ni le schéma de sa requête, ni les
variables `NOVA_DEPLOYMENT_APPLY_TOKEN` et
`NOVA_ALLOW_LEGACY_DEPLOYMENT_TOKEN`.

Poser ces variables ne produit **aucun effet**. Il n'y a pas de mode de
compatibilité, pas de repli implicite, pas d'acceptation accompagnée d'un
avertissement : la route répond `404` quel que soit l'environnement, parce
qu'elle n'est plus enregistrée. Des tests de source et de configuration
empêchent sa réintroduction.

Un chemin hérité ne meurt pas quand on cesse de le documenter — il meurt quand
poser sa variable ne change plus rien.

**Migration d'un déploiement antérieur**

1. passer le point d'entrée à `python serve.py` ;
2. déclarer `NOVA_CONNECTOR_LOCAL_TRANSPORT=unix` et le socket partagé ;
3. retirer `NOVA_DEPLOYMENT_APPLY_TOKEN` de l'environnement et du compose ;
4. enrôler un Connector depuis Nova Admin s'il n'y en a pas déjà un.

Un script ou un outil qui appelait l'ancienne route doit passer par le
Connector. Il n'y a pas d'équivalent direct côté réseau : c'est délibéré, la
surface interne n'étant plus servie sur le listener public.

Aucune compatibilité cachée ne subsiste : la seule frontière entre le Connector
et nova-server est le canal local, et la seule frontière entre Nova et le
Connector est un credential rotatable.

Codes de refus : `AI_RUNTIME_CONFIG_REJECTED`, `AI_RUNTIME_PROFILE_UNAVAILABLE`,
`AI_RUNTIME_HARDWARE_INCOMPATIBLE`.

## Présence

`online` → `degraded` (2 battements manqués) → `offline` (4 battements). Un
Connector révoqué reste `revoked` : il ne redevient pas « hors ligne » parce
qu'il a cessé d'appeler. La distinction dit à l'administrateur si sa décision
tient toujours.

Le plan de contrôle date tout lui-même. L'horloge du Connector est un
renseignement de diagnostic, jamais une autorité.

## Autonomie du plan de données — invariant P0

Si Nova est injoignable, le Connector se contente de réessayer avec un backoff
exponentiel et de la dispersion. Le plan de données continue de servir la
dictée, la reformulation, les Styles, les AI Skills et le runtime local avec la
dernière configuration appliquée.

Perdre le plan de contrôle retarde une reconfiguration. Il n'interrompt aucun
usage.

## Isolation multi-tenant

L'organisation d'un Connector vient de la base, retrouvée par l'empreinte du
credential présenté. Aucun champ de requête ne peut l'influencer. Un Connector
ne peut donc ni lire l'état souhaité d'un autre tenant, ni acquitter pour lui,
ni voir ses diagnostics.

## Modèle de menace

| Menace | Mitigation |
|---|---|
| Credential volé | Empreinte seule côté serveur ; révocation immédiate ; portée limitée à un tenant et à deux opérations typées ; aucun accès à du contenu utilisateur |
| Rejeu | Mutations idempotentes par révision ; `applied_revision` ne recule jamais ; HTTPS |
| Hôte du Connector compromis | Le Connector ne détient aucun contenu utilisateur ; il ne peut qu'appliquer une configuration que le plan de contrôle a produite pour son propre tenant |
| Administrateur d'un tenant malveillant | Ne peut créer d'enrôlement que pour son organisation ; `deployment_manage` requis ; tout est audité |
| Confusion inter-tenant | L'organisation vient de l'authentification, jamais du corps |
| Panne du plan de contrôle | Le plan de données reste autonome ; backoff exponentiel avec dispersion |
| MITM | HTTPS obligatoire ; HTTP refusé sauf demande explicite en développement |
| Reporting excessif | Limites de charge utile, plafond de capacités, cadence limitée **par Connector** — un bavard ne fait pas taire ses voisins |
| Credential cloud vieillissant | Rotation en ligne sans ré-enrôlement ; âge exposé à la console |
| Fuite du secret local statique | Le mécanisme n'existe plus : ni route, ni variable, ni comparaison. Permissions de socket en production, jeton éphémère par démarrage en développement |
| Confusion entre les deux frontières | Deux credentials distincts, deux principals distincts ; le credential cloud n'est jamais transmis au plan de données, et le secret local n'est jamais rapporté au cloud |
| Dégradation de protocole | Version explicite, refus propre, aucune retombée silencieuse |

## Ce que le Connector n'apporte pas

- Pas d'auto-mise à jour du Connector (phase ultérieure).
- Pas de rotation périodique automatique : la rotation existe, elle se déclenche
  à la demande.
- Pas de parité Windows sur le canal local.
- Pas de distribution de poids de modèles.
- Pas de proxy vers l'infrastructure privée.

## Confidentialité — formulation exacte

> Le Connector synchronise des métadonnées de contrôle uniquement. Les prompts,
> l'audio dicté, les transcriptions et les sorties de modèle restent dans le
> plan de données de l'organisation.

Cette phrase est vraie parce que le protocole ne comporte aucun champ capable de
les transporter, et que les tests le vérifient sur la charge utile réellement
émise — pas sur la prose de ce document.
