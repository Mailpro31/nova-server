# Déploiement en entreprise — architecture

Nova est installable poste par poste depuis toujours. Ce document décrit ce
qu'il faut de plus pour qu'une DSI l'installe sur mille postes sans toucher à
aucun d'eux, et sans qu'un secret d'organisation se retrouve dans un package.

Le guide opérateur correspondant est
[`docs/deployment/windows-enterprise.md`](../deployment/windows-enterprise.md).

---

## 1. Ce qui existait déjà — matrice d'audit

L'audit a précédé le code. Il porte sur le chemin réel :
installation → `ProgramData` → résolution de configuration → découverte
d'organisation → connexion → Control Plane → Connector → Data Plane.

| Artefact | Portée | Emplacement | Propriétaire | Source de configuration | Secret ? | Mise à jour | Rollback | Désinstallation | Diagnostic |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Nova Desktop — édition Organization | machine | `C:\Program Files\Nova\` | DSI (SYSTEM) | `tauri.campus.conf.json` → NSIS `perMachine` | non | updater Tauri (NSIS) → `nova-releases/latest.json` | aucun mécanisme atomique | clé `Uninstall` HKLM + `uninstall.exe` | aucun avant cette phase |
| Nova Desktop — édition Personal | utilisateur | `%LOCALAPPDATA%\Nova\` | utilisateur | `tauri.conf.json` (perUser) | non | même updater | idem | clé `Uninstall` HKCU | idem |
| Installation portable | dossier | dossier choisi | utilisateur | marqueur `portable` + `Data\` | non | aucune | — | suppression du dossier | — |
| Configuration machine (héritée) | machine | `%ProgramData%\Nova\campus-config.json` | DSI | fichier déposé par la DSI | non | remplacement du fichier | — | non supprimée par l'installeur | — |
| Configuration à côté de l'exe (héritée) | installation | `<INSTDIR>\campus-config.json` | DSI | fichier déposé | non | écrasée par une mise à jour | — | supprimée avec `INSTDIR` | — |
| Session utilisateur | utilisateur | trousseau Windows + `campus_session.json` | utilisateur | SSO | **oui** (jeton) | — | — | conservée | — |
| Connector privé | serveur Linux | conteneur Docker | DSI | enrôlement one-time (Phase 35) | **oui** (credential durable local) | releases signées, orchestrateur | version précédente | `docker rm` | `/api/control/connectors` |
| Release Connector | Control Plane | table `connector_releases` | Nova | manifeste signé | non | immuable, révocable | révocation | — | `/api/control/connector-releases` |
| Release Desktop | GitHub `nova-releases` | `latest.json` + `.exe` + `.sig` | Nova | workflow `nova-release.yml` | non | minisign (updater) | aucun | — | aucun |

### Ce que l'audit a réellement trouvé

1. **`perMachine` existe déjà**, par `tauri.campus.conf.json`, et la CI sait
   produire les deux éditions (`nova-windows.yml`, entrée `nova_mode`). La
   fondation est là ; ce qui manquait était tout ce qui vient après le double-clic.
2. **Aucune installation ne crée `%ProgramData%\Nova`.** Le code sait le lire
   depuis la Phase Campus, mais rien ne l'écrit : la DSI devait déposer le
   fichier par un second mécanisme. Vérifié sur l'hôte de développement, où une
   Nova `perMachine` 1.0.36 est installée sans aucun `C:\ProgramData\Nova`.
3. **La découverte d'organisation est un contrat sans appelant.**
   `discover_organization` est exposée dans `bindings.ts` et n'est appelée par
   aucune interface : le seul chemin réel reste `server_url` en mode dédié.
4. **Aucune origine Control Plane n'est épinglée.** L'adresse vient entièrement
   du fichier déposé. Acceptable pour un pilote dédié, insuffisant pour un
   package officiel : une politique MDM pourrait désigner n'importe quelle
   adresse.
5. **Aucune identité de déploiement.** Rien ne relie un poste à une organisation
   avant la connexion, donc rien à diagnostiquer côté Control Plane.
6. **Collision de nom.** `deployment_id` existe déjà en Phase 35 comme
   *étiquette libre* d'un Connector (`connector_enrollments.deployment_id`,
   « prod-paris »). Le `deployment_id` de cette phase est un identifiant
   **opaque généré par le Control Plane** pour un parc de postes. Deux notions,
   deux tables, aucune jointure : documenté ici pour que personne ne les confonde.
7. **Personal et Organization partagent tout** : `productName` `Nova`,
   identifiant `app.novaspeak.desktop`, clé de désinstallation `…\Uninstall\Nova`,
   identité updater, entrée de démarrage. Seul le *scope* (HKCU/HKLM,
   `LOCALAPPDATA`/`Program Files`) les distingue. C'est la contrainte centrale
   de la coexistence, traitée au §7.
8. **Le downgrade est déjà refusé** par le template NSIS (`dontUninstallDowngrade`).
9. **Aucune signature de code.** Le setup produit est `NotSigned` : vérifié sur
   l'artefact 1.0.36. Seule la signature *updater* (minisign) existe.
10. **Le MSI existe** — Tauri/WiX le produit à chaque build — mais il n'a ni
    template personnalisé, ni chemin updater, ni injection de configuration.

---

## 2. Architecture cible

```
NOVA CONTROL PLANE
        |
        ├── DeploymentProfile        (organisation, canal, édition, révision)
        ├── Bootstrap metadata       (non secrète, publiable)
        ├── Release metadata         (version, artefact, sha256, canal)
        └── Deployment diagnostics   (état déclaré par les postes)
                 |
                 v
POSTE WINDOWS
        |
        ├── Nova Desktop             C:\Program Files\Nova\
        ├── Configuration machine    C:\ProgramData\Nova\
        └── Connector (optionnel)    — en pratique sur un serveur Linux
                 |
                 v
DATA PLANE PRIVÉ
```

Le package ne contient **aucun secret d'organisation durable**. Il contient une
origine Control Plane épinglée et, au plus, un identifiant de déploiement
opaque. Ni l'un ni l'autre n'authentifie qui que ce soit.

---

## 3. Décision de format d'installeur

**NSIS `perMachine`**, et pas un MSI.

Le MSI existe déjà — Tauri/WiX en produit un à chaque build — et il aurait
ouvert la voie à GPO Software Installation. Il a été écarté pour trois raisons
qui tiennent toutes au même point : ce serait un second installeur à maintenir.

1. **L'updater de Tauri consomme l'artefact NSIS.** Publier un MSI comme paquet
   officiel donnerait un parc que la mise à jour intégrée ne sait pas mettre à
   jour.
2. **Le template NSIS est déjà personnalisé** — mode portable, amorçage
   WebView2, refus de downgrade. Toute cette logique devrait être réécrite en
   fragments WiX, et les deux versions divergeraient.
3. **Deux installeurs, c'est deux identités de désinstallation** pour un même
   produit. C'est précisément la collision que le §7 cherche à éviter.

Conséquence assumée : **GPO Software Installation n'est pas supportée.** Le
guide opérateur documente le déploiement par script de démarrage, et ne prétend
pas le contraire. Le MSI continue d'être produit par le build ; il n'est pas
publié comme paquet d'organisation.

---

## 4. Frontière du secret

Rien de durable et de secret ne traverse le package.

| Élément | Où il vit | Secret ? |
| --- | --- | --- |
| `deployment_id` | ligne de commande, `ProgramData`, registre | non — n'authentifie personne |
| `organization_id` | idem | non |
| `control_plane_origin` | **compilé dans le binaire** | non |
| `release_channel` | idem | non |
| Session utilisateur | trousseau Windows, profil utilisateur | **oui** |
| Credential SCIM | Control Plane, client de provisioning | **oui** |
| Credential Connector | serveur du Connector | **oui** |

Le contrat le rend structurel plutôt que déclaratif : le schéma du poste est en
`deny_unknown_fields`, donc un champ `token` ajouté au fichier machine ne serait
pas ignoré — il rendrait la configuration **invalide**, et le poste refuserait
de se dire géré. Un test le fige pour six noms de champ secrets courants.

---

## 5. Priorité des sources de configuration

```
HKLM\SOFTWARE\Policies\Nova        (GPO / MDM)
        > %ProgramData%\Nova\organization.json   (installeur)
        > valeurs par défaut du package
        > campus-config.json                     (hérité)
```

**Une source prioritaire invalide n'est pas contournée.** Elle échoue, et le
poste se déclare non géré avec un code d'erreur. Se rabattre sur la source
suivante permettrait à un utilisateur de contourner une stratégie en cassant
volontairement une valeur qu'il peut lire ; c'est le contraire de ce qu'une
stratégie doit garantir.

Codes d'erreur, tous stables et sûrs à journaliser :

`DEPLOYMENT_CONFIG_MALFORMED`, `…_SCHEMA_UNSUPPORTED`, `…_FIELD_MISSING`,
`…_VALUE_INVALID`, `…_FIELD_UNKNOWN`, `…_ORIGIN_NOT_PINNED`, `…_UNREADABLE`.

### Repli Personal (`PersonalFallback`)

La stratégie machine porte aussi une valeur qui n'appartient pas au contrat de
déploiement : `PersonalFallback` (`DWORD`). À `0`, un poste Organization sans
session — révoquée, expirée ou fermée — exige une nouvelle connexion avant toute
dictée ; absente ou à `1`, il continue en Personal, avec la dictée locale.

Elle vit **uniquement** dans le registre, lue indépendamment de `Managed`, et
jamais dans `organization.json` : ce schéma est en `deny_unknown_fields`, et un
champ nouveau y rendrait le fichier invalide pour tous les postes déjà
déployés. Un poste plus ancien ignore, lui, une valeur de registre qu'il ne
connaît pas.

Le refus est appliqué par le poste lui-même, pas seulement par l'écran : sans
session et avec le repli interdit, le moteur n'ouvre plus aucun enregistrement,
puisque les raccourcis globaux restent actifs derrière l'écran de connexion.

---

## 6. Origine du Control Plane

Épinglée dans le binaire (`PINNED_CONTROL_PLANE_ORIGINS`). Une stratégie MDM
**désigne** une origine de cette liste ; elle ne peut pas en inventer une.

Sans cela, déployer une stratégie serait un moyen de rediriger les échanges d'un
poste vers un service arbitraire — et une DSI compromise, ou simplement un
modèle ADMX copié depuis un mauvais endroit, suffirait. Quatre variantes sont
testées et refusées : une autre origine, la même en `http`, un suffixe
(`api.novaspeak.app.evil.example`), et une barre oblique finale.

Un contournement de développement passe par un binaire compilé avec une autre
liste — pas par une variable d'environnement lisible en production.

---

## 7. Coexistence avec l'installation Personal

Personal et Organization partagent `productName`, identifiant d'application,
clé de désinstallation, identité updater et entrée de démarrage. Seule la portée
les distingue : `HKCU` + `%LOCALAPPDATA%` contre `HKLM` + `Program Files`.

**Décision : la coexistence n'est pas supportée, et elle n'est pas empêchée.**

Windows permet les deux installations en parallèle — l'hôte de développement en
porte d'ailleurs une de chaque. Elles ne s'écrasent pas : les scopes sont
disjoints. Mais deux exécutables partagent alors le même identifiant
d'application, donc le même répertoire de données utilisateur et la même entrée
de démarrage — le dernier installé gagne.

Ce que Nova fait :

- l'installeur `perMachine` **ne désinstalle jamais** une installation
  personnelle. Supprimer les données d'un utilisateur sans son consentement
  serait une décision que ni Nova ni un outil MDM n'ont à prendre ;
- la portée d'installation est **déduite du chemin** de l'exécutable et exposée
  dans le diagnostic, pour que la situation soit visible plutôt que devinée ;
- le guide opérateur demande la désinstallation manuelle de la version
  personnelle avant le déploiement, et explique pourquoi.

Rendre la coexistence réellement supportée demanderait deux identifiants
d'application, donc deux répertoires de données et deux updaters. C'est un
changement de produit, pas un détail d'installeur, et il n'appartient pas à
cette phase.

---

## 8. Identité de poste

Un `device_id` aléatoire de 128 bits (`RtlGenRandom`), écrit **une seule fois**
par l'installeur dans `%ProgramData%\Nova\device.json`.

- non secret ;
- **jamais dérivé du matériel** — ni numéro de série, ni TPM, ni adresse MAC ;
- conservé par une mise à jour, renouvelé par une réinstallation ;
- absent tant que l'installeur ne l'a pas écrit, et le poste le dit alors
  plutôt que d'en fabriquer un.

Il n'y a **pas d'enregistrement de poste** dans cette phase, et donc pas de
question d'authentification par appareil : connaître un poste n'accorderait
rien de toute façon. L'identifiant existe pour distinguer deux rapports d'état
le jour où un canal de rapport existera.

---

## 9. Ce que Nova n'apprend pas

Aucun poste ne rapporte quoi que ce soit à Nova dans cette phase. La console de
déploiement l'affiche explicitement (`device_reporting: false`) et ne renvoie
**jamais** l'état `healthy` : il exigerait une mesure qui n'existe pas.

C'est aussi pourquoi cette phase n'ajoute aucune analytique. Le seul élargissement
serait un rapport de déploiement — version, `deployment_id`, révision de
configuration, résultat de mise à jour — et il devra être décidé pour lui-même,
pas glissé dans une phase d'installation.

---

## 10. Où vit quoi

Le déploiement traverse les deux dépôts, et il vaut mieux le dire que le laisser
deviner.

| Élément | Dépôt | Fichier |
| --- | --- | --- |
| Résolution de la configuration machine | `nova-desktop` | `src-tauri/src/deployment.rs` |
| Template d'installeur | `nova-desktop` | `src-tauri/nsis/installer.nsi` |
| Configuration d'édition Organization | `nova-desktop` | `src-tauri/tauri.campus.conf.json` |
| Profils de déploiement, releases | `nova-server` | `deployment.py` |
| Console d'administration | `nova-server` | `admin-web/src/pages/DesktopDeployment.tsx` |
| Guide opérateur | `nova-server` | `docs/deployment/windows-enterprise.md` |

Le contrat partagé — canaux, politiques de mise à jour, anneaux, forme de
l'identifiant — est écrit deux fois, une fois par langage. Les deux listes
doivent rester identiques : une valeur acceptée par le Control Plane et refusée
par le poste produirait un poste géré qui refuse de démarrer. Des tests figent
les deux côtés.
