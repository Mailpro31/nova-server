# Runbook de pilote — Nova Organization

De « rien » à « des membres qui dictent », puis retour.

Ce document décrit un parcours **reproductible**. Chaque étape passe par une
commande d'exploitation ou par la console — aucune n'ouvre la base à la main.
C'est vérifié par `test_pilot_acceptance.py`, qui exécute ce parcours sur une
base PostgreSQL neuve à chaque exécution de la suite.

Compléments : [inventaire des données](./data-inventory.md),
[checklist](./pilot-checklist.md),
[runbook de production](../operations/production-runbook.md),
[déploiement Windows](../deployment/windows-enterprise.md).

---

## 1. Préparer le tenant

Une instance sert une organisation. Déclarez-la — **la production refuse de
démarrer sans** :

```
NOVA_ENV=production
NOVA_DATABASE_URL=postgresql://…
NOVA_REDIS_URL=redis://…
NOVA_ORGANIZATION_ID=<slug>
NOVA_ORGANIZATION_NAME=<libellé>
NOVA_DOMAINS=<domaines autorisés, séparés par des virgules>
NOVA_SERVICE_ENDPOINT=https://…
NOVA_ADMIN_ORIGINS=https://…
```

Les valeurs par défaut de ces variables portent le nom du tout premier
déploiement. Les laisser en production ferait servir votre organisation sous
l'identité d'une autre, avec les apparences d'une configuration correcte : le
démarrage les refuse donc explicitement.

`NOVA_DOMAINS` mérite une mention à part, parce que son défaut est le plus
discret des trois. Il ne bloque pas le démarrage et n'empêche pas
l'administrateur de se connecter — son compte existe déjà, et le rattachement
d'identité précède le contrôle de domaine. Il refuse **tous les autres
membres**, à leur première connexion, avec `MEMBERSHIP_NOT_FOUND`. Une
installation peut donc paraître réussie tant qu'on ne teste que
l'administrateur.

**Sur une installation faite par l'installeur Windows, cette variable n'est
plus à écrire à la main :** elle est saisie par l'installeur (voir §3) et posée
dans la configuration générée. L'installeur refuse d'écrire une configuration
sans elle.

Puis :

```
python -m migrations status
python -m migrations migrate
```

Le service ne migre jamais tout seul. Il refuse de démarrer sur une base en
retard ou plus récente que lui.

---

## 2. Configurer le fournisseur d'identité

Microsoft Entra, Google Workspace ou OIDC générique, via Nova Admin →
Identity. Le secret client se déclare par référence :

```
NOVA_OIDC_CLIENT_SECRET=file:///run/secrets/oidc_client_secret
```

La console affiche l'**existence** d'un secret, jamais sa valeur.

Le code à six chiffres par e-mail reste disponible et suffit à amorcer un pilote
si l'IdP n'est pas encore prêt — c'est d'ailleurs par lui que le premier
administrateur obtiendra son compte.

---

## 3. Créer le premier administrateur

**Il n'existe aucun endpoint « faites de moi un administrateur ».** Un tel
endpoint resterait une porte dérobée quelles que soient ses conditions. Il n'y a
pas non plus de variable d'amorçage, qui resterait en place bien après le
premier démarrage.

### Sur une installation faite par l'installeur Windows

**Il n'y a rien à faire : l'installeur s'en charge.** Il demande l'adresse du
premier administrateur avant de télécharger quoi que ce soit, puis, une fois
`/api/readyz` obtenu, exécute la promotion dans le conteneur `api`. L'écran
final affiche les administrateurs réellement enregistrés.

```
NovaOrganizationServerSetup.exe -OrganizationDomains exemple.fr -FirstAdminEmail alex@exemple.fr
NovaOrganizationServerSetup.exe -OrganizationDomains exemple.fr,cadres.exemple.fr `
    -FirstAdminEmail alex@exemple.fr -SecondAdminEmail bea@exemple.fr
```

`-OrganizationDomains` est **obligatoire** : ce sont les domaines de courriel
dont les adresses pourront se connecter. Il n'a pas de valeur par défaut, parce
que celle du serveur appartient à une autre organisation — voir §2. Une adresse
d'administrateur hors de ces domaines est refusée tout de suite : elle serait
promue sans jamais pouvoir se connecter.

En mode silencieux (`-Headless`), `-OrganizationDomains` et `-FirstAdminEmail`
sont **obligatoires** : l'installation s'arrête immédiatement si l'un manque,
avant tout téléchargement, plutôt que de livrer un serveur inutilisable. En mode
interactif, l'installeur pose les deux questions.

Les réponses sont retenues dans un fichier protégé, avec les mêmes droits
d'accès que le mot de passe de la base : la reprise après le redémarrage exigé
par WSL2 relance l'installeur **sans aucun argument**, et sans cette mémoire
elle régénérerait la configuration avec les valeurs par défaut. Le nom et le
type d'organisation suivent le même chemin, pour la même raison. Aucune adresse
n'apparaît dans un journal.

Relancer l'installation, `update` ou `reconfigure` rejoue la promotion sans
effet de bord : `grant` **écrit** un rôle, il ne l'ajoute pas, et l'unicité
(organisation, adresse) interdit le doublon.

### À la main, ou sur une installation Linux

Le parcours est en deux temps :

1. la personne se connecte normalement — par SSO ou par code e-mail. Son compte
   naît de cette connexion ;
2. sur le serveur, quelqu'un qui le détient déjà exécute :

```
python admin_cli.py grant alex@exemple.fr organization_admin
python admin_cli.py list
```

Depuis un poste où la pile tourne dans des conteneurs :

```
docker compose exec -T api python admin_cli.py list
```

### Nommer quelqu'un qui ne s'est jamais connecté

`grant` refuse une adresse inconnue, et c'est le bon défaut : sur le chemin de
récupération, une faute de frappe doit s'arrêter là. Pour l'amorçage d'une
organisation neuve, où **aucun** compte n'existe encore, ajoutez
`--create-if-missing` :

```
python admin_cli.py grant alex@exemple.fr organization_admin --create-if-missing
```

Le compte ainsi créé n'est pas un compte parallèle. À la première connexion
réelle, le fournisseur d'identité s'y rattache par l'adresse et **le réutilise**
— le rôle survit, aucun doublon n'apparaît.

> **L'adresse doit être exactement celle que renvoie le fournisseur
> d'identité.** C'est elle, et elle seule, qui fait le rattachement. Une adresse
> approchante — un alias, ou l'écart entre l'UPN et la boîte aux lettres chez
> Entra — produit un administrateur qui ne correspondra jamais à personne,
> pendant que la personne réelle se connecte et obtient un compte ordinaire. En
> cas de doute, vérifiez avec `admin_cli list` après la première connexion.

Rôles : `organization_admin` gère les personnes et le produit ; `it_admin` gère
la machine — déploiement, fournisseurs, diagnostics ; `read_only` observe.
**Aucun n'est un sur-ensemble des autres**, et `grant` remplace le rôle au lieu
de l'ajouter.

Prévoyez **au moins deux** `organization_admin` : voir §17.

---

## 4. Configurer le déploiement

Nova Admin → Deployment, en tant qu'`it_admin` :

1. créer un profil (nom, canal `stable`, politique de mise à jour) ;
2. copier la commande d'installation ;
3. récupérer la configuration machine.

Le profil ne porte aucun secret. L'identifiant de parc est opaque et
n'authentifie personne.

---

## 5. Configurer les policies

Nova Admin → Policies, en tant qu'`organization_admin`. Une policy décrit ce que
l'organisation offre à ses membres. La console distingue « laissé par défaut »
de « choisi ainsi » : le jour où un défaut produit change, les organisations qui
n'avaient rien demandé suivent.

Le poste applique les policies **à sa prochaine synchronisation**, pas
instantanément. Ce n'est pas du temps réel, et il ne faut pas le présenter
comme tel.

---

## 6. Publier les paquets

Styles d'organisation, AI Skills, vocabulaire. Une version publiée est
**immuable** : corriger produit une nouvelle version. Revenir en arrière consiste
à pointer la version active vers une version antérieure, pas à modifier
l'ancienne.

Une AI Skill ne transporte que son identifiant jusqu'au poste ; l'instruction
réelle reste côté serveur.

---

## 7. Déployer le Connector

Sur un serveur Linux de l'organisation, pas sur les postes.

1. Nova Admin → Deployment → Private Connector, créer un code d'enrôlement — à
   usage unique, de courte durée, **affiché une seule fois** ;
2. lancer le Connector avec ce code, sur un volume d'état persistant ;
3. il échange le code contre un credential durable stocké localement. Le
   Control Plane n'en garde que l'empreinte.

Un code consommé ne se réutilise pas.

---

## 8. Configurer l'AI Runtime

Nova Admin → Deployment → AI Runtime. Choisir le profil, appliquer, vérifier que
`desired` et `applied` convergent.

Si le runtime privé est indisponible, Nova **échoue clairement**. Il n'existe
pas de bascule silencieuse vers un service cloud : ce serait envoyer le contenu
de l'organisation ailleurs sans que personne l'ait demandé.

---

## 9. Générer l'installeur

Voir [`windows-enterprise.md`](../deployment/windows-enterprise.md). Édition
Organization, NSIS `perMachine`, `requireAdministrator`.

L'installeur n'accepte **ni** `SERVER_URL`, **ni** `COMMAND`, **ni** `SCRIPT`,
**ni** `TOKEN`. L'origine du Control Plane est compilée dans le binaire.

---

## 10. Déployer les postes

> **Après toute modification du paquet**, exécutez le workflow
> `organization-windows-pilot-test.yml`. Il installe le
> paquet sur un runner Windows jetable, vérifie les ACL de `ProgramData`, la
> clé de détection et l'absence de trace dans le profil utilisateur, puis
> désinstalle, réinstalle et purge. C'est la seule validation qui prouve que
> l'installeur s'installe : une machine de développement portant déjà Nova ne
> peut pas servir de banc d'essai.


```
NovaOrganizationSetup.exe /S /DEPLOYMENT_ID=<id> /ORGANIZATION_ID=<org> /CHANNEL=stable
```

Via Intune, SCCM, GPO par script de démarrage, ou tout MDM. Codes de sortie et
règle de détection dans le guide de déploiement.

Désinstallez d'abord une éventuelle version personnelle : les deux peuvent
coexister sans s'écraser, mais elles partagent le répertoire de données
utilisateur.

---

## 11. Première connexion d'un membre

1. le poste lit sa configuration machine et se sait géré ;
2. il valide le schéma — un champ inconnu le fait échouer, il ne devine pas ;
3. il joint le Control Plane à l'origine épinglée ;
4. **l'utilisateur s'authentifie par SSO** : un poste géré n'est ni un
   utilisateur authentifié, ni un administrateur de confiance ;
5. le jeton va dans le trousseau du système ;
6. policies et paquets arrivent.

Aucun écran ne demande d'adresse de serveur. Si un écran le demande, la
configuration machine n'a pas été lue — vérifiez `organization.json` et les
ACL.

---

## 12. Vérifier

Côté serveur :

```
curl https://…/api/livez     # le processus vit
curl https://…/api/readyz    # base, schéma, compteur partagé
```

Côté console : Deployment, Identity, Connector, AI Runtime, Packages, Audit.

Côté poste :

```
reg query "HKLM\SOFTWARE\Nova\Deployment"
type C:\ProgramData\Nova\organization.json
icacls C:\ProgramData\Nova
```

Aucun de ces diagnostics ne montre de contenu utilisateur.

---

## 13. Support

Ce qu'il faut demander à un utilisateur : version, canal, édition, source de
configuration, identifiant de parc, état de session. **Jamais** un jeton, ni un
extrait de dictée.

Erreurs courantes :

| Symptôme | Cause probable | Geste |
| --- | --- | --- |
| Écran demandant une adresse de serveur | configuration machine absente ou illisible | vérifier `organization.json` et les ACL |
| « Configuration de déploiement invalide » | schéma invalide, ou origine non épinglée | comparer à l'exemple fourni |
| Connexion refusée après SSO | compte non membre, ou suspendu | vérifier le statut dans la console |
| Session perdue au redémarrage | trousseau inaccessible sur le profil | vérifier le profil Windows |
| Dictée indisponible | runtime privé injoignable | Nova Admin → AI Runtime |
| Configuration figée | Connector déconnecté | Nova Admin → Connector |

---

## 14. Rollback

| Objet | Procédure |
| --- | --- |
| Paquet | pointer la version active vers une version antérieure |
| Policy | remettre la clé à son défaut |
| Profil AI Runtime | réappliquer le profil précédent |
| Mise à jour Connector | l'orchestrateur redéploie la version précédente |
| Release Desktop | **pas de retour arrière atomique.** L'installeur NSIS n'est pas transactionnel : on réinstalle la version voulue, et le downgrade est refusé par défaut |

---

## 15. Désinstaller et réinstaller

```
"C:\Program Files\Nova\uninstall.exe" /S
```

Conserve la configuration machine et les données utilisateur. Une réinstallation
avec le même `DEPLOYMENT_ID` retrouve donc son organisation.

Purge explicite :

```
"C:\Program Files\Nova\uninstall.exe" /S /PURGE_CONFIG
```

---

## 16. Fin de pilote

Dans cet ordre :

1. déprovisionner les membres (console ou SCIM) — les sessions tombent ;
2. révoquer les credentials Connector et SCIM ;
3. retirer les profils de déploiement — les nouvelles installations ne sont plus
   reconnues ;
4. désinstaller les postes, avec `/PURGE_CONFIG` si l'organisation le décide ;
5. retirer la configuration du fournisseur d'identité ;
6. sauvegarder la base une dernière fois, puis **décider explicitement** de
   l'archiver ou de la détruire. Rien n'est supprimé automatiquement.

---

## 17. Si tous les administrateurs sont perdus

Il n'existe **aucun jeton maître permanent**. C'est délibéré : un tel jeton
serait une porte dérobée permanente.

La récupération passe par qui détient le serveur :

```
python admin_cli.py list
python admin_cli.py grant <adresse> organization_admin
```

La personne doit d'abord posséder un compte — donc s'être connectée au moins une
fois. Si plus personne ne peut se connecter, il faut d'abord rétablir le
fournisseur d'identité, ou utiliser le code par e-mail. En dernier recours,
`--create-if-missing` nomme quelqu'un qui ne s'est jamais connecté ; relisez
l'avertissement du §3 avant de vous en servir : hors amorçage, une adresse
approchante crée un administrateur qui ne désigne personne.

**Conséquence pratique : nommez au moins deux `organization_admin` dès le début
du pilote.** La garde qui empêche de retirer le dernier gestionnaire d'identités
protège contre l'erreur, pas contre le départ d'une personne.
