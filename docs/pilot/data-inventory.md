# Inventaire des données — pilote Nova Organization

Ce que Nova stocke, où, pourquoi, et ce qu'il ne stocke pas.

Ce document décrit le comportement du logiciel. Il ne constitue ni un avis
juridique, ni une analyse d'impact : c'est la matière première dont votre DPO a
besoin pour en produire une.

---

## 1. La frontière, en une phrase

**Le Control Plane porte des identités et de la configuration. Le contenu de
travail ne le traverse jamais.**

Ni audio, ni transcription, ni prompt, ni presse-papiers, ni historique de
dictée. Ce n'est pas une politique de rétention — c'est une absence : ces
données n'ont pas de table où atterrir.

Vérifié sur le schéma : sur 269 colonnes, les seules occurrences des mots
« transcription » et « audio » sont `usage.transcriptions` (un compteur entier)
et `usage.audio_seconds` (une durée). Aucune colonne binaire.

---

## 2. Ce que le Control Plane stocke

| Donnée | Où | Pourquoi | Rétention | Qui y accède | Contenu utilisateur ? | Suppression |
| --- | --- | --- | --- | --- | --- | --- |
| Compte : `user_id`, e-mail, e-mail normalisé, rôle métier, rôle de sécurité, statut | `users` | identifier la personne et ce qu'elle a le droit de faire | vie de l'organisation | `identity_manage` | non — l'adresse est un identifiant | déprovisionnement (statut), suppression administrative |
| Identité fédérée : sujet externe immuable, émetteur, tenant, vérification | `federated_identities` | relier un compte à son IdP sans dépendre de l'adresse | idem | `identity_manage` | non | avec le compte |
| Session de poste : empreinte du jeton, machine, expiration | `tokens` | authentifier un poste | jusqu'à expiration ou révocation | serveur | non | déconnexion, suspension, expiration |
| Session d'administration | `admin_sessions` | ouvrir la console | courte, révocable | serveur | non | révocation, changement de rôle |
| Code à usage unique | `auth_codes` | premier facteur par e-mail | quelques minutes | serveur | non | consommation ou expiration |
| État de flux SSO | `sso_flows` | corréler départ et retour d'un parcours SSO | courte | serveur | non | consommation ou expiration |
| Organisation : identifiant, slug, libellé, type, découverte, mode de déploiement | `organizations` | identité du tenant | vie de l'organisation | admin | non | fin de pilote |
| Configuration de fournisseur d'identité | `organization_provider_configs` | se connecter à l'IdP | idem | `provider_manage` | non — **le secret client n'est jamais renvoyé**, seule son existence l'est | suppression de la configuration |
| Policies produit | `organization_policies` | ce que l'organisation offre à ses membres | idem | `policy_manage` | non | remise aux défauts |
| Paquets : Styles, AI Skills, vocabulaire, et leurs versions | `organization_packages`, `organization_package_versions` | contenu publié **par l'organisation** | idem | `content_manage` | oui, mais **produit par l'administrateur**, jamais par l'utilisateur | archivage, suppression |
| Profils de déploiement | `deployment_profiles` | décrire un parc | idem | `deployment_manage` | non | retrait |
| Connectors et enrôlements | `connectors`, `connector_enrollments` | identifier une passerelle privée | idem | `deployment_manage` | non — **empreinte du credential seulement** | révocation |
| Credentials SCIM | `scim_credentials` | provisioning entrant | idem | `provisioning_manage` | non — **empreinte seulement** | révocation |
| État SCIM : `externalId`, `userName`, nom, département | `scim_user_management` | réconcilier avec l'annuaire | idem | `provisioning_manage` | non | déprovisionnement |
| Groupes et appartenances | `organization_groups`, `organization_group_memberships` | refléter l'annuaire | idem | `provisioning_manage` | non | suppression |
| Journal d'audit administratif | `admin_audit_log` | qui a fait quoi | **à définir par l'organisation** | `diagnostics_read` | non — métadonnées seulement | non purgé automatiquement |
| Activité SCIM | `scim_activity` | diagnostiquer le provisioning | idem | `provisioning_manage` | non — codes d'erreur | — |
| Compteurs d'usage : nombre de transcriptions, secondes d'audio | `usage` | quota et diagnostic | idem | admin | **non — des nombres, pas du contenu** | avec le compte |
| Dictionnaire personnel, dictionnaire partagé, snippets, règles de formatage | `personal_dictionary`, `shared_dictionary`, `snippets`, `formatting_rules` | vocabulaire et raccourcis | vie du compte | l'utilisateur, admin pour le partagé | **oui — du texte saisi par l'utilisateur** | suppression par l'utilisateur ou avec le compte |

### Le seul contenu utilisateur du Control Plane

Le dictionnaire personnel, les snippets et les règles de formatage contiennent
du texte que l'utilisateur a écrit. C'est de la **configuration**, pas de la
dictée : des mots à reconnaître, des abréviations à développer. Il faut
néanmoins le dire, parce que c'est le seul endroit où du texte utilisateur
existe côté serveur.

---

## 3. Ce que le Control Plane ne stocke pas

- audio, quelle qu'en soit la forme ;
- transcriptions ;
- prompts envoyés à un modèle ;
- réponses d'un modèle ;
- contenu du presse-papiers ;
- historique de dictée ;
- contenu d'écran ou capture.

Ces données vivent sur le poste et, pour l'inférence, dans le Data Plane privé
de l'organisation. Elles ne traversent pas le Control Plane, même en transit.

---

## 4. Le poste

| Donnée | Où | Contenu utilisateur ? |
| --- | --- | --- |
| Jeton de session | **trousseau du système** | non |
| Métadonnées de session (adresse, organisation) | magasin applicatif | non |
| Configuration machine | `C:\ProgramData\Nova\organization.json` | non — et le schéma refuse tout champ inconnu, secret compris |
| Identifiant de poste | `C:\ProgramData\Nova\device.json` | non — 128 bits aléatoires, **jamais dérivé du matériel** |
| Historique et réglages locaux | profil utilisateur | oui |

Le jeton n'est jamais écrit dans `organization.json`, ni dans le registre, ni
dans un journal.

---

## 5. Observabilité

Les métriques ne portent aucune étiquette identifiante : ni utilisateur, ni
organisation, ni parc, ni connecteur. Une route comme
`/api/control/organizations/{id}/members` est comptée sous la classe `control`.

Les journaux techniques caviardent les champs nommés `token`, `password`,
`transcript`, `prompt`, `email` et leurs voisins. Ils sont distincts du journal
d'audit métier, qui doit rester.

Aucune télémétrie utilisateur n'existe, et cette phase n'en ajoute aucune.

---

## 6. Sauvegardes

Une sauvegarde du Control Plane contient tout le tableau du §2 — donc des
adresses e-mail et des empreintes de session. Elle ne contient aucun identifiant
de connexion à la base, et aucun contenu de travail.

Elle doit être traitée comme une donnée personnelle : chiffrement au repos,
accès restreint, rétention décidée. Le chiffrement relève du stockage, pas de
Nova.

---

## 7. Fin de pilote

Voir la procédure dans [`pilot-runbook.md`](./pilot-runbook.md) §16. En résumé :
déprovisionner les membres, révoquer les credentials Connector et SCIM, retirer
les profils de déploiement, désinstaller les postes, puis décider explicitement
du sort de la base — l'archiver ou la détruire. Rien n'est supprimé
automatiquement.
