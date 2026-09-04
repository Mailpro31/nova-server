# Checklist de pilote — Nova Organization

À cocher au fur et à mesure. **Notez ce qui échoue plutôt que de le contourner :
un échec observé vaut mieux qu'un échec contourné.**

Le détail de chaque étape est dans [`pilot-runbook.md`](./pilot-runbook.md).

---

## Avant le pilote

### Infrastructure

- [ ] PostgreSQL 16 provisionné, sauvegardé, chiffré au repos
- [ ] Redis provisionné (obligatoire en production)
- [ ] TLS terminé au reverse proxy, certificat valide
- [ ] `NOVA_TRUSTED_PROXIES` renseigné si un proxy est devant
- [ ] Serveur Linux prêt pour le Connector
- [ ] Runtime d'inférence privé accessible depuis le Connector

### Configuration

- [ ] `NOVA_ENV=production`
- [ ] `NOVA_DATABASE_URL` et `NOVA_REDIS_URL` renseignés
- [ ] `NOVA_ORGANIZATION_ID`, `NOVA_ORGANIZATION_NAME`, `NOVA_DOMAINS`
      renseignés — **les défauts portent le nom d'une autre organisation**.
      Sur une installation faite par l'installeur Windows, `NOVA_DOMAINS` est
      saisi par l'installeur (`-OrganizationDomains`) et non écrit à la main
- [ ] `NOVA_DOMAINS` **vérifié en connectant quelqu'un qui n'est pas
      administrateur** : son défaut ne bloque ni le démarrage ni
      l'administrateur, seulement tous les autres membres
- [ ] `NOVA_SERVICE_ENDPOINT` et `NOVA_ADMIN_ORIGINS` en HTTPS publiques
- [ ] Secrets déclarés par référence (`env://` ou `file://`), aucun secret faible
- [ ] Aucun drapeau `*_ALLOW_INSECURE`
- [ ] Le démarrage est accepté par la validation de production

### Décisions à prendre avec l'organisation

- [ ] Rétention du journal d'audit décidée
- [ ] Rétention et chiffrement des sauvegardes décidés
- [ ] Périmètre du pilote arrêté : combien de personnes, quels postes
- [ ] Interlocuteur support désigné des deux côtés
- [ ] [Inventaire des données](./data-inventory.md) transmis au DPO

---

## Jour 0 — mise en place

- [ ] `python -m migrations status` puis `migrate`
- [ ] `/api/livez` et `/api/readyz` répondent
- [ ] Fournisseur d'identité configuré et testé avec **un** compte
- [ ] Domaines de l'organisation déclarés, et une adresse **hors** de ces
      domaines confirmée refusée
- [ ] Premier administrateur nommé — par l'installeur Windows
      (`-FirstAdminEmail`, rien à faire de plus), ou par `admin_cli grant`
      (`--create-if-missing` si la personne ne s'est jamais connectée)
- [ ] Adresse du premier administrateur **relue** : ce doit être exactement
      celle que renvoie le fournisseur d'identité, sans quoi le compte promu ne
      correspondra jamais à personne — voir runbook §3
- [ ] **Au moins deux `organization_admin`** — `-SecondAdminEmail`, ou la
      commande donnée par l'écran final ; voir runbook §17
- [ ] `admin_cli list` montre les administrateurs attendus
- [ ] Le premier administrateur s'est connecté **pour de vrai** et ouvre Nova
      Admin : c'est cette connexion qui prouve le rattachement
- [ ] Profil de déploiement créé, commande d'installation copiée
- [ ] Configuration machine récupérée et relue
- [ ] Policies revues, y compris celles laissées par défaut
- [ ] Paquets publiés si le pilote en prévoit
- [ ] Connector enrôlé, code consommé, statut en ligne
- [ ] AI Runtime appliqué, `desired` = `applied`
- [ ] Sauvegarde initiale prise et **vérifiée** (`python -m backup verify`)

---

## Jour 1 — premiers postes

- [ ] Installeur Organization déployé sur **un** poste témoin
- [ ] Version personnelle éventuelle désinstallée au préalable
- [ ] `HKLM\SOFTWARE\Nova\Deployment` présent
- [ ] `C:\ProgramData\Nova\organization.json` présent et conforme
- [ ] ACL vérifiées : un membre standard ne peut pas réécrire le fichier
- [ ] Premier lancement : **aucun écran ne demande d'adresse de serveur**
- [ ] Connexion SSO réussie, organisation reconnue
- [ ] Policies et paquets reçus
- [ ] Une dictée aboutit de bout en bout
- [ ] Déconnexion puis reconnexion fonctionnent
- [ ] Déploiement élargi au reste du périmètre

---

## Chaque semaine

- [ ] `/api/readyz` vert
- [ ] Sauvegarde du jour vérifiée
- [ ] Journal d'audit parcouru
- [ ] Connector en ligne, dernière synchronisation récente
- [ ] AI Runtime sain
- [ ] Aucun 5xx inattendu dans les métriques
- [ ] Limitation de débit : aucun pic anormal
- [ ] Postes à jour sur le canal choisi
- [ ] Retours utilisateurs collectés

---

## Une fois pendant le pilote — les exercices

Ce sont ceux qu'on regrette de ne pas avoir faits.

- [ ] **Restauration** : restaurer la sauvegarde dans une base neuve et vérifier
      les effectifs. Jamais par-dessus la production.
- [ ] **Rotation du credential Connector**, sans interruption
- [ ] **Rollback de paquet** vers une version antérieure
- [ ] **Déprovisionnement** d'un compte de test : la session tombe
- [ ] **Perte du Connector** : la dictée continue-t-elle comme documenté ?
- [ ] **Perte du runtime privé** : message clair, aucune bascule cloud
- [ ] **Désinstallation puis réinstallation** d'un poste témoin

---

## Fin de pilote

- [ ] Membres déprovisionnés
- [ ] Credentials Connector et SCIM révoqués
- [ ] Profils de déploiement retirés
- [ ] Postes désinstallés, `/PURGE_CONFIG` si décidé
- [ ] Configuration du fournisseur d'identité retirée
- [ ] Sauvegarde finale prise et vérifiée
- [ ] Sort de la base décidé **explicitement** : archivage ou destruction
- [ ] Compte rendu écrit : ce qui a marché, ce qui a échoué, ce qui manque

---

## État de validation des recettes

Ce qui a été exécuté contre de la vraie infrastructure, et ce qui ne l'a pas été.

| Recette | État | Preuve |
| --- | --- | --- |
| Amorçage d'un tenant neuf, sans SQL manuel | **exécutée** | PostgreSQL 16 neuf, `test_pilot_acceptance.py` |
| Rôles, isolation de tenants, déprovisionnement | **exécutée** | idem |
| Charge d'un pilote | **exécutée** | 50 lectures, 20 synchronisations, 30 authentifications |
| Reformulation par moteur privé | **exécutée** | LLM réel servi en API OpenAI |
| Style d'organisation, instruction server-side | **exécutée** | une instruction envoyée par le client n'écrase rien |
| Transcription par moteur privé | **exécutée** | Whisper réel, audio **synthétique** |
| Panne puis reprise du moteur | **exécutée** | erreur contrôlée, aucune bascule cloud |
| Enrôlement Connector, code à usage unique | **exécutée** | vrai serveur HTTP, vrai processus Connector |
| Reconnexion sans nouvel enrôlement | **exécutée** | idem |
| Rotation de credential sans interruption | **exécutée** | C1 valide jusqu'à la preuve de C2, puis révoqué |
| Rotation interrompue | **exécutée** | le Connector reste authentifié |
| **Installation Windows réelle** | **exécutée** | `organization-windows-pilot-test.yml`, run 33247650112, Windows Server 2025 jetable |
| Mise à jour Connector, signature invalide | non rejouée sur processus réel | suite Phase 35 verte |
| SSO Entra réel | non revalidé | suites existantes vertes |

L'installation Windows réelle ne peut pas se valider sur une machine de
développement : elle porte de vraies installations Nova, et Windows Famille
n'offre ni Sandbox ni Hyper-V. Le workflow installe, désinstalle, réinstalle et
purge sur un runner jetable, et vérifie les ACL réelles — par SID, car les noms
de groupes sont localisés. **Rejouez-le après toute modification du paquet ou de
son contrat d'installation** : il se déclenche d'ailleurs seul sur les pull
requests qui y touchent.

Il a déjà trouvé ce qu'aucune lecture n'avait vu : le chemin de la
configuration Organization était résolu depuis la racine du dépôt et non depuis
`src-tauri`, si bien qu'aucun paquet Organization n'aurait pu se construire.

---

## Points connus avant de commencer

Ce ne sont pas des surprises, ce sont des limites documentées.

- [ ] L'installeur **n'est pas signé Authenticode** : SmartScreen avertira
- [ ] **GPO Software Installation n'est pas supportée** (pas de MSI) — script de
      démarrage ou MDM
- [ ] Les policies s'appliquent à la **prochaine synchronisation**, pas en temps réel
- [ ] Il n'y a **pas de retour arrière atomique** d'une mise à jour Desktop
- [ ] La rotation du credential Connector est **manuelle**
- [ ] Nova ne reçoit **aucun rapport d'état des postes** : la console ne montre
      donc aucune santé de parc, et n'en invente pas
