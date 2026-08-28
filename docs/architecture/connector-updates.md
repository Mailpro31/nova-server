# Distribution et mise à jour du Private Connector

## Le problème

Un Connector déployé chez un client va vieillir. Il faut pouvoir le remplacer
sans envoyer quelqu'un sur place, et sans que Nova acquière au passage le
pouvoir d'exécuter du code arbitraire sur l'infrastructure d'une organisation.

Ces deux exigences tirent en sens contraire. Tout ce qui suit consiste à les
tenir ensemble.

## Ce que le plan de contrôle n'envoie jamais

Pas de commande. Pas de shell, pas de chemin d'exécutable, pas d'arguments.
L'intention est **typée** et tient en un champ :

```json
{ "target_version": "0.2.0" }
```

Le Connector sait comment se mettre à jour. Un canal capable de le lui dicter
serait un shell distant sous un autre nom, et c'est précisément ce que le
Private Connector existe pour éviter.

Le binaire ne passe pas non plus par la synchronisation : le mélanger au plan de
contrôle ferait de Nova un maillon de la chaîne d'approvisionnement.

## La chaîne de confiance

TLS dit d'où vient un octet, pas qui l'a produit. Trois maillons, dans cet
ordre :

```
signature du manifeste  →  empreinte de l'artefact  →  activation
```

1. **Le manifeste est signé** (Ed25519, signature détachée). Il est vérifié
   contre une clé publique que le déploiement connaît d'avance
   (`NOVA_CONNECTOR_RELEASE_PUBLIC_KEY`).
2. **L'artefact est vérifié par empreinte** contre ce manifeste signé — donc
   contre quelque chose dont l'authenticité est déjà établie.
3. **L'activation** n'a lieu qu'après. Jamais avant.

Rompre le premier maillon suffit à tout arrêter. Sans clé configurée, rien
n'est accepté : un repli permissif « en attendant » ouvrirait exactement ce que
cette vérification protège.

La signature est vérifiée **avant** que le moindre champ du manifeste soit lu.
Analyser d'abord laisserait un manifeste non signé influencer une décision,
ne serait-ce qu'en faisant lever une erreur différente selon son contenu.

### Racine de confiance

Une clé publique Ed25519, configurée par le déploiement. Le plan de contrôle
refuse de **publier** une release dont la signature ne vérifie pas : s'il
acceptait, la vérification côté Connector deviendrait la seule barrière de
toute la chaîne, et un plan de contrôle compromis suffirait à distribuer
n'importe quoi.

### Rotation de la clé de signature

Non implémentée, et conçue pour l'être. Le modèle prévoit une clé courante et
une clé suivante, la seconde étant publiée par le processus de release lui-même
avant de devenir la première. En cas de compromission de la clé : retirer les
releases concernées, changer la clé, republier. Ce chemin est documenté ici ;
il n'est pas automatisé, et prétendre le contraire donnerait une fausse
assurance.

## Deux modes de distribution — deux contrats

Un conteneur ne peut pas remplacer sa propre image. Lui donner le socket Docker
pour qu'il essaie reviendrait à lui donner la machine — bien plus grave que le
problème que cela résoudrait.

Ce ne sont donc pas « une capacité et son absence », mais **deux contrats
explicites** :

| Mode | Capacités déclarées | Qui applique |
|---|---|---|
| `package` | `connector_update_v1`, `connector_self_update_v1` | le Connector lui-même |
| `docker` | `connector_update_v1`, `connector_orchestrator_update_v1` | le déploiement |

Le plan de contrôle n'envoie **jamais** un ordre d'auto-mise à jour à un
Connector qui ne déclare pas `connector_self_update_v1` : il resterait « en
cours de mise à jour » indéfiniment, et la console afficherait une opération qui
n'arriverait jamais.

Le mode est **déclaré** (`NOVA_CONNECTOR_DISTRIBUTION`), jamais deviné, et la
console suit la capacité — pas l'OS. Un même Linux peut porter les deux.

### Le superviseur

En mode `package`, un processus témoin lance le Connector et observe son code de
sortie :

```
0   arrêt demandé
10  bascule faite, relance-moi
11  retour arrière fait, relance-moi
```

Ce sont les seuls ordres qu'un Connector lui adresse. Le superviseur ne sait
lancer que le `connector.py` de sa propre installation, avec l'interpréteur qui
l'exécute déjà : pas de shell, pas d'argument venu du réseau, pas d'exécutable
désigné de l'extérieur.

Un programme ne peut pas se remplacer lui-même de façon fiable — s'il échoue
juste après la bascule, il n'y a plus personne pour revenir en arrière. C'est
précisément ce cas que le témoin extérieur couvre : une version qui ne démarre
pas n'exécute aucun code de retour arrière, et c'est le superviseur qui
rebascule.

### La frontière de redémarrage

```
Connector vN  ──activate──▶  exit 10  ──▶  superviseur  ──▶  Connector vN+1
                                                                    │
                                             santé constatée ───────┤──▶ completed
                                             santé absente  ───────┘──▶ exit 11 → vN
```

Le nouveau processus doit prouver : la bonne version, un credential encore
valide, un plan de contrôle joignable, un canal local qui répond. Sans quoi il
revient à la dernière version connue saine. « Le processus a démarré » ne vaut
pas « la mise à jour a réussi ».

Nova temporairement injoignable n'est **pas** une mise à jour ratée : la santé
locale et la reconnexion au plan de contrôle sont deux signaux distincts, et
seul le second peut manquer sans conséquence.

### Ce qui survit à une mise à jour

```
state/      identité, credential, rotation, révisions, transaction
versions/   0.1.0/  0.2.0/
current ->  versions/0.2.0
```

L'identité et le credential vivent **hors** des répertoires versionnés. Les y
mettre ferait qu'une mise à jour effacerait l'identité du Connector en même
temps que son code.

Là où les liens symboliques ne sont pas disponibles — Windows sans droit dédié
— la bascule est un fichier de pointage écrit atomiquement, et la résolution
passe par la version installée plutôt que par un répertoire `current/`.

### Protection contre la boucle

Une cible qui a échoué n'est pas retentée avant expiration d'un délai. Sans
cela : mauvaise version → retour arrière → le plan de contrôle repropose la même
cible → nouvelle tentative → boucle. Le plan de contrôle doit voir l'échec, et
une nouvelle tentative doit être un acte explicite.

### Exclusion mutuelle avec la rotation

Une bascule de version ne démarre pas pendant une transition de credential
(`CONNECTOR_UPDATE_BLOCKED_BY_ROTATION`), et l'inverse est vrai. Deux machines à
états critiques simultanées produiraient des combinaisons que personne n'a
raisonnées.

## Machine à états

```
up_to_date → update_available → downloading → staged → verifying
                                                          ↓
    healthy ← restarting ← applying ←──────────────────────┘
       │
       └─(santé en échec)→ rollback_in_progress → rolled_back
```

Plus `failed` et `unsupported`.

**Le succès ne s'annonce jamais parce qu'un téléchargement est terminé.** Il
suppose : signature vérifiée, empreinte confirmée, activation faite, service
relancé, santé constatée, Connector reconnecté avec un protocole valide.

## Téléchargement

Borné en taille (128 Mo) et en durée (300 s). Espace disque vérifié avant de
commencer, avec de la marge pour l'archive, sa décompression et la version
conservée pour le retour arrière.

Le fichier n'apparaît sous son nom définitif qu'une fois complet : une coupure
réseau laisse un fichier temporaire, jamais un artefact à moitié écrit qu'une
reprise prendrait pour valide. Un artefact dont l'empreinte ne correspond pas
est **supprimé** — le garder inviterait à le réutiliser un jour « pour gagner
du temps ».

### La source appartient au déploiement

`NOVA_CONNECTOR_RELEASE_HOSTS` liste les hôtes autorisés. HTTPS obligatoire,
redirections non suivies. Si le plan de contrôle pouvait désigner n'importe
quelle adresse, un administrateur — ou quiconque prendrait sa place — ferait
télécharger et exécuter du code arbitraire par tous les Connectors d'un tenant.

## Activation atomique

Chaque version vit dans son propre répertoire ; `current` est un lien qui
désigne l'une d'elles. La bascule est un remplacement de lien : à tout instant,
`current` pointe vers une version complète — l'ancienne ou la nouvelle, jamais
un état intermédiaire.

L'archive est déballée avec refus des chemins absolus, des remontées `..`, des
liens et des fichiers spéciaux : la façon classique de transformer une archive
en écriture arbitraire.

## Retour arrière

Instantané, parce que l'ancienne arborescence est encore là. Un retour arrière
qui dépendrait du réseau échouerait précisément quand le réseau est le problème.

Deux versions sont conservées — la courante et la précédente. En garder vingt
ne servirait qu'à remplir le disque du client : on ne revient jamais qu'à la
dernière connue saine.

## Downgrade

Installer une version antérieure est refusé. Le retour en arrière passe par le
chemin de rollback, qui sait vers quelle version saine revenir ; le faire passer
par « installer une version plus ancienne » permettrait de rejouer une release
retirée.

## Immuabilité et retrait

Une release publiée est **immuable** : même version, même contenu. Corriger un
artefact se fait en publiant une nouvelle version, jamais en changeant le
contenu d'une version déjà distribuée — sinon toute vérification d'empreinte
devient caduque.

Une release peut être **retirée** (`revoked`). Elle cesse d'être installable.
Les Connectors qui la portent déjà ne sont pas coupés : les débrancher ferait
d'un problème de chaîne d'approvisionnement une panne de service.

## Version minimale supportée

Un Connector plus ancien que le minimum est **signalé**, pas débranché. Couper
sa synchronisation le priverait de la mise à jour qui le remettrait à niveau.

Le protocole et la version produit restent distincts : une correction de bogue
ne doit pas obliger tout un parc à renégocier ce qu'il sait faire.

## Autonomie — invariant P0

Pendant un téléchargement, une bascule ou un retour arrière, nova-server
continue de servir. Si le Connector redémarre, le plan de données local ne
s'interrompt pas : il tourne sur la dernière configuration appliquée.

Une mise à jour ne perd jamais l'identité du Connector, son credential, une
rotation en cours, ni la dernière révision appliquée.

## Modèle de menace

| Menace | Mitigation |
|---|---|
| Serveur de release compromis | Le manifeste est signé ; un artefact substitué échoue à la signature ou à l'empreinte |
| MITM | HTTPS, hôtes en allowlist, redirections non suivies — et la signature couvre le reste |
| Artefact altéré | Empreinte SHA-256 vérifiée contre un manifeste signé ; l'artefact douteux est supprimé |
| Clé de signature volée | Retrait des releases concernées, changement de clé, republication. Chemin documenté, non automatisé |
| Rejeu d'une ancienne release | Downgrade refusé ; le retour arrière passe par le rollback local |
| Attaque par downgrade | Comparaison SemVer numérique, pas textuelle |
| Administrateur de tenant malveillant | Ne peut demander qu'une version publiée, pour ses propres Connectors ; aucune URL, aucune commande |
| URL arbitraire | Allowlist d'hôtes contrôlée par le déploiement, pas par le message reçu |
| Épuisement disque | Espace vérifié avant téléchargement, avec marge |
| Mise à jour interrompue | Fichier partiel jamais activé ; état local atomique ; reprise ou retour arrière au redémarrage |
| Staging corrompu | Empreinte vérifiée avant activation ; staging nettoyé |
| Hôte du Connector compromis | Rien ici ne protège d'un root local. Ce qui est visé : la chaîne d'approvisionnement, pas la machine |

## Ce que cette phase ne fait pas

- Pas de mise à jour automatique activée par défaut. La politique par défaut
  est manuelle : une bascule silencieuse sur un parc n'est pas une décision
  que Nova doit prendre à la place d'une DSI.
- Pas de rotation automatisée de la clé de signature.
- Pas de parité Windows revendiquée : le mode `package` y fonctionne — la
  bascule, le retour arrière et le superviseur ont été exercés sur Windows avec
  de vrais processus — mais la bascule y repose sur un fichier de pointage
  plutôt qu'un lien symbolique, et le durcissement des permissions n'y est pas
  effectif.
- Pas d'orchestration de flotte. Une mise à jour vise un Connector.
