# Déployer Nova sur un parc Windows

Guide opérateur. Il s'adresse à une DSI qui installe Nova sur des postes qu'elle
administre, avec Intune, SCCM/MECM, Workspace ONE, un RMM ou ses propres
scripts.

L'architecture et les décisions sont dans
[`docs/architecture/enterprise-deployment.md`](../architecture/enterprise-deployment.md).

---

## 1. Ce que vous déployez

| | |
| --- | --- |
| Paquet | `NovaOrganizationSetup.exe` (NSIS, `perMachine`) |
| Répertoire | `C:\Program Files\Nova\` |
| Configuration machine | `C:\ProgramData\Nova\organization.json` |
| Journaux machine | `C:\ProgramData\Nova\logs\` |
| Clé de détection | `HKLM\SOFTWARE\Nova\Deployment` |
| Systèmes supportés | Windows 10 et 11, x64 |
| Élévation | requise pour installer, **pas** pour utiliser |

ARM64 n'est pas publié : aucun build ARM64 n'existe, et annoncer une plateforme
non compilée ferait échouer un déploiement au lieu de le refuser proprement.

---

## 2. Avant de déployer

1. **Désinstallez la version personnelle** si elle est présente
   (`%LOCALAPPDATA%\Nova`). Les deux installations peuvent coexister sans
   s'écraser, mais elles partagent le répertoire de données utilisateur et
   l'entrée de démarrage : le comportement dépendrait alors de l'ordre
   d'installation. L'installeur ne la supprime jamais tout seul — supprimer les
   données d'un utilisateur sans son accord n'est pas une décision d'installeur.
2. **Créez un profil de déploiement** dans Nova Admin → Deployment. Il vous
   donne un `deployment_id` opaque et la commande d'installation à copier. La
   page exige la capacité `deployment_manage`, portée par le rôle `it_admin`.

---

## 3. Installation silencieuse

```
NovaOrganizationSetup.exe /S /DEPLOYMENT_ID=<id> /ORGANIZATION_ID=<org> /CHANNEL=stable
```

Copiez-la depuis Nova Admin plutôt que de la composer : la console produit
exactement les identifiants que le poste acceptera.

### Paramètres acceptés

| Paramètre | Valeurs | Rôle |
| --- | --- | --- |
| `/S` | — | installation silencieuse |
| `/DEPLOYMENT_ID=` | `[A-Za-z0-9_-]{1,128}` | parc, opaque |
| `/ORGANIZATION_ID=` | idem | organisation, opaque |
| `/CHANNEL=` | `stable` ou `preview` | canal de diffusion |
| `/MANAGED_CONFIG=` | chemin local | fichier `organization.json` préparé |

`/DEPLOYMENT_ID` et `/ORGANIZATION_ID` vont ensemble : l'un sans l'autre est
refusé.

### Paramètres délibérément absents

Il n'existe **ni** `SERVER_URL`, **ni** `COMMAND`, **ni** `ARGS`, **ni**
`SCRIPT`, **ni** `TOKEN`, **ni** `PASSWORD`, **ni** `SECRET`.

Un installeur qui accepterait une adresse de serveur libre ferait de chaque
stratégie de déploiement une autorité sur la destination des échanges du poste.
Un installeur qui accepterait une commande serait un moyen d'exécution
arbitraire déclenchable par n'importe quel outil de gestion de parc. L'origine
du Control Plane est compilée dans le binaire.

### Codes de sortie

| Code | Émis par | Signification |
| --- | --- | --- |
| `0` | installeur | succès |
| `1` | installeur | échec générique |
| `2` | installeur | paramètres de déploiement invalides |
| `3` | installeur | version de Windows non supportée |
| `6` | installeur | mise à jour refusée (downgrade) |
| `1223` | Windows | élévation refusée par l'utilisateur (UAC annulé) |

`4` et `5` sont **réservés** — privilèges insuffisants et installation existante
en conflit — et ne sont pas émis aujourd'hui : l'élévation est gérée par Windows
(`RequestExecutionLevel admin`), et un conflit d'installation se manifeste par le
refus de downgrade ou par un échec générique. Ils sont listés pour que le
contrat n'ait pas à changer de numérotation le jour où ils le seront ; ne les
configurez pas comme codes attendus dans un outil de déploiement.

---

## 4. Configuration machine

L'installeur écrit `C:\ProgramData\Nova\organization.json` :

```json
{
  "schema_version": 1,
  "deployment_id": "dpl-0123456789abcdef0123456789abcdef",
  "organization_id": "org-exemple",
  "control_plane_origin": "https://api.novaspeak.app",
  "release_channel": "stable",
  "managed": true,
  "config_revision": 1
}
```

Un exemple complet et commenté :
[`organization-config.example.json`](./organization-config.example.json).

**Aucun secret n'y figure, et aucun ne peut y figurer** : le poste refuse tout
champ qu'il ne connaît pas. Ajouter un `token` ne le rendrait pas seulement
dangereux — il rendrait la configuration invalide, et le poste refuserait de se
dire géré.

### ACL

L'installeur pose des ACL explicites sur `C:\ProgramData\Nova` :

| Principal | Droits |
| --- | --- |
| `SYSTEM` | contrôle total |
| `Administrateurs` | contrôle total |
| `Utilisateurs` | lecture et exécution |

L'héritage est coupé : l'ACL héritée de `ProgramData` laisse un utilisateur
standard créer des fichiers, et une configuration qu'un utilisateur peut
réécrire n'est pas une configuration gérée. Les SID sont utilisés en clair
(`*S-1-5-32-544`), parce que les noms de groupes sont traduits.

Vérification :

```
icacls C:\ProgramData\Nova
```

---

## 5. Stratégie de groupe / MDM (registre)

`HKLM\SOFTWARE\Policies\Nova` a la priorité sur le fichier. Un utilisateur
standard ne peut pas l'écrire.

| Valeur | Type | Contenu |
| --- | --- | --- |
| `Managed` | `DWORD` | `1` pour activer ; `0` ou absent = clé ignorée |
| `DeploymentId` | `SZ` | identifiant de parc |
| `OrganizationId` | `SZ` | organisation |
| `ControlPlaneOrigin` | `SZ` | doit appartenir à la liste épinglée |
| `Channel` | `SZ` | `stable` ou `preview` |
| `UpdatePolicy` | `SZ` | `manual`, `notify` ou `automatic` |
| `ConfigRevision` | `DWORD` | révision |
| `Autostart` | `DWORD` | démarrage à l'ouverture de session |
| `PersonalFallback` | `DWORD` | `0` : un poste sans session exige une nouvelle connexion avant toute dictée ; `1` ou absent : Nova continue en Personal |

`PersonalFallback` est lue **indépendamment de `Managed`** : elle peut être posée
seule, y compris sur un poste configuré par `organization.json`. Elle ne
concerne que l'absence de session — révoquée, expirée ou fermée. Un membre
suspendu par l'administrateur, ou un poste simplement hors ligne, n'est pas
concerné. Les versions de Nova qui ne connaissent pas cette valeur l'ignorent.

Ne l'ajoutez **pas** à `organization.json` : ce fichier refuse tout champ
inconnu, et le poste se déclarerait alors non géré.

Priorité : **registre > `ProgramData` > défauts du package > `campus-config.json`
(hérité)**. Une stratégie invalide **fait échouer** la résolution ; elle ne se
rabat pas silencieusement sur le fichier. Se rabattre laisserait un utilisateur
contourner une stratégie en cassant volontairement une valeur qu'il peut lire.

---

## 6. Intune (Win32)

| Champ | Valeur |
| --- | --- |
| Type d'application | Application Windows (Win32) |
| Commande d'installation | `NovaOrganizationSetup.exe /S /DEPLOYMENT_ID=<id> /ORGANIZATION_ID=<org> /CHANNEL=stable` |
| Commande de désinstallation | `"C:\Program Files\Nova\uninstall.exe" /S` |
| Comportement d'installation | **System** |
| Comportement au redémarrage | Aucune action spécifique |
| Codes de retour | `0` = Succès ; `2`, `3`, `6` = Échec |
| Prérequis | Windows 10 1809 ou ultérieur, x64 |
| Stratégie de mise à jour | Supersedence, version précédente non désinstallée |

### Règle de détection

Utilisez le registre, jamais la présence d'un processus : un poste où Nova n'est
pas lancé reste un poste où Nova est installé.

| Champ | Valeur |
| --- | --- |
| Type | Registre |
| Chemin | `HKEY_LOCAL_MACHINE\SOFTWARE\Nova\Deployment` |
| Nom de la valeur | `Version` |
| Méthode | Comparaison de version |
| Opérateur | Supérieur ou égal à |
| Valeur | la version déployée, par exemple `1.0.37` |

Cette clé est écrite par l'installeur `perMachine` uniquement ; une installation
personnelle ne la crée pas, donc la détection ne confond pas les deux éditions.

### Désinstallation

`uninstall.exe /S` retire les binaires, la clé de désinstallation, la clé de
détection et l'entrée de démarrage machine. Il **conserve** la configuration
machine et les données utilisateur.

Pour purger aussi la configuration machine :

```
"C:\Program Files\Nova\uninstall.exe" /S /PURGE_CONFIG
```

C'est une action administrative distincte, et c'est délibéré : une réparation ou
une mise à jour ne doit jamais rompre le rattachement à l'organisation. Les
données d'un compte personnel ne sont supprimées dans aucun des deux cas.

---

## 7. GPO

**GPO Software Installation n'est pas supportée.** Elle exige un MSI, et le
paquet d'organisation est un installeur NSIS — voir la décision de format dans
le document d'architecture.

Ce qui fonctionne :

- **script de démarrage machine** appelant la commande silencieuse, gardé par la
  clé de détection pour ne pas réinstaller à chaque démarrage ;
- **préférence de registre GPO** écrivant `HKLM\SOFTWARE\Policies\Nova`.

---

## 8. SCCM/MECM, Workspace ONE, RMM

L'installeur ne contient aucune logique propre à un fournisseur : la commande
silencieuse, les codes de sortie et la clé de détection sont les mêmes partout.

---

## 9. Mise à jour, downgrade, réparation

- **N vers N+1, silencieuse** : la configuration machine, les données
  utilisateur, le trousseau et le rattachement à l'organisation sont préservés.
  La configuration vit hors du répertoire d'installation, donc rien à recopier.
  Relancer l'installeur **sans** paramètre de déploiement ne la réécrit pas.
- **Downgrade** : refusé, code de sortie `6`. Il n'existe pas de commutateur
  générique pour passer outre.
- **Réparation** : réinstaller la même version répare les binaires sans toucher
  à la configuration.
- **Échec de mise à jour** : l'installeur NSIS n'est pas transactionnel. Une
  interruption en cours d'écriture peut laisser une installation incomplète, que
  l'on répare en relançant l'installeur. Il n'existe **pas** de retour arrière
  atomique, et il ne faut pas en promettre un.

---

## 10. Premier lancement

1. lecture de la stratégie machine, puis du fichier machine ;
2. validation stricte du schéma ;
3. le poste se sait dans un déploiement géré ;
4. découverte / Control Plane à l'origine épinglée ;
5. **SSO** — c'est l'utilisateur qui s'authentifie, pas la machine ;
6. session ;
7. policies et paquets applicables ;
8. prêt.

Aucun écran ne demande une adresse de serveur. Un déploiement géré n'est ni un
utilisateur authentifié, ni un administrateur de confiance : le parcours SSO
reste entier.

**Hors ligne** : un poste géré reste géré. Il n'y a pas de repli en édition
personnelle, et la configuration machine ne dépend d'aucun appel réseau.

**Configuration invalide** : le poste indique que la configuration de
déploiement de l'organisation est invalide, expose le code d'erreur, et ne se
rabat sur aucune adresse.

---

## 11. Diagnostic

Le poste expose son état de déploiement : version installée, édition, géré ou
non, source de la configuration, `deployment_id`, révision, canal, politique de
mise à jour, `device_id`, portée d'installation, et code d'erreur éventuel.

Aucun contenu utilisateur n'y figure : ni texte dicté, ni audio, ni
presse-papiers, ni historique.

Vérifications côté machine :

```
reg query "HKLM\SOFTWARE\Nova\Deployment"
type C:\ProgramData\Nova\organization.json
icacls C:\ProgramData\Nova
```

---

## 12. Signature de code

L'installeur produit aujourd'hui **n'est pas signé Authenticode**. Le paquet est
prêt à l'être — la chaîne de build accepte une commande de signature — mais
aucun certificat de production n'existe.

Conséquence à ne pas maquiller : **SmartScreen affichera un avertissement**. Une
mise en production exige un certificat de signature de code reconnu, et la
réputation SmartScreen se construit ensuite avec les téléchargements. Aucune
option d'installeur ne supprime cet avertissement, et aucune ne devrait le
prétendre.

Une clé privée ou un certificat privé n'est jamais versionné dans ce dépôt.

---

## 13. Connector

Le Connector privé n'est pas déployé sur les postes. Il tourne sur un serveur
Linux de l'organisation, en conteneur Docker géré par l'orchestrateur maison.

1. Nova Admin → Deployment → Private Connector : créer un **code d'enrôlement**.
   Il est à usage unique, de courte durée, et affiché **une seule fois** ;
2. lancer le Connector avec ce code, sur un volume d'état persistant ;
3. le Connector échange le code contre un credential durable, stocké
   localement — jamais dans un script permanent ni dans une image ;
4. les mises à jour suivent des releases signées ; l'orchestrateur en est
   responsable, et la version précédente reste le retour arrière.

Un Connector Windows n'existe pas. Ce n'est pas un obstacle au déploiement des
postes Windows : le Data Plane est prévu côté serveur.

---

## 14. Ce qui n'est pas fait

- **MSI d'organisation**, donc pas de GPO Software Installation ;
- **signature Authenticode de production** ;
- **rapport d'état des postes vers Nova** : la console ne connaît donc aucune
  santé de parc, et ne prétend pas en connaître ;
- **anneaux de déploiement dynamiques** : le champ existe, le ciblage n'existe
  pas ;
- **fenêtre de maintenance** ;
- **validation sur un tenant Intune réel** et **installation réelle sur un poste
  isolé** : voir le rapport de phase pour les raisons exactes.
