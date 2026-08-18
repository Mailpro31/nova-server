# Nova Admin

Console web d'administration d'une organisation Nova.

Ce n'est **pas** Nova Desktop, ni Nova Control (l'outil interne de l'équipe
Nova), ni un tableau de bord de surveillance des utilisateurs. C'est la console
technique d'**une** organisation cliente : ses fournisseurs d'identité, sa
découverte, son déploiement, sa sécurité d'administration.

Architecture et décisions :
[`docs/architecture/nova-admin-foundation.md`](../../nova-desktop/docs/architecture/nova-admin-foundation.md)
(dépôt `nova-desktop`, où vit la documentation d'architecture).

---

## Développement

```bash
bun install
cp .env.example .env      # puis renseigner VITE_NOVA_API_URL
bun run dev               # http://127.0.0.1:5174
```

**`127.0.0.1`, jamais `localhost`.** Le serveur refuse les URI de redirection
portant un nom d'hôte — leur résolution ne se maîtrise pas — et Entra compare
l'adresse de retour telle quelle. Ouvrir la console sur `localhost:5174` fait
échouer la connexion avant même d'atteindre le fournisseur.

Le serveur Nova doit tourner et **autoriser l'origine de la console** :

```bash
# côté nova-server
NOVA_ADMIN_ORIGINS=http://127.0.0.1:5174
```

Sans cette variable, aucune origine n'est autorisée — un déploiement sans console
web n'ouvre rien.

## Variables d'environnement

| Variable | Rôle |
|---|---|
| `VITE_NOVA_API_URL` | Origine du Control Plane. À défaut, l'origine de la page. |
| `VITE_NOVA_DEFAULT_ORG` | Organisation ouverte par défaut en développement. Facultatif. |

**Aucune adresse réelle ne doit être commitée.** `.env` est ignoré ; seul
`.env.example` est suivi, avec des valeurs locales.

## Commandes

```bash
bun run dev      # serveur de développement
bun run build    # typecheck + build de production (dist/)
bun run test     # tests unitaires
```

## Authentification

```
identifiant d'organisation  →  découverte  →  adresse du service
                            →  SSO de l'organisation
                            →  session utilisateur Nova
                            →  réauthentification (step-up)
                            →  session d'administration
```

Il n'y a **aucun mot de passe Nova** : l'identité vient du fournisseur de
l'organisation. Administrer exige en plus une authentification *récente* — c'est
le step-up de la Phase 24, et une session utilisateur seule n'y donne pas accès.

### Où vivent les jetons

**En mémoire, et nulle part ailleurs.** Ni `localStorage`, ni `sessionStorage`,
ni cookie, ni URL.

Un jeton d'administration dans `localStorage` est lisible par tout script qui
s'exécute dans la page, et il survit à la fermeture de l'onglet : le vol devient
permanent. En mémoire, il disparaît au rafraîchissement — l'administrateur
repasse par la réauthentification, ce qui coûte quelques secondes.

Conséquence : **il n'y a pas de CSRF à défendre sur ce chemin**. Rien n'est
ambiant, donc le navigateur n'envoie rien automatiquement ; une page tierce peut
déclencher une requête, elle ne peut pas y joindre le jeton. `credentials:
"omit"` est explicite dans le client, et un test le vérifie.

La cible reste un cookie `HttpOnly` + `Secure` + `SameSite`, qui demandera alors
une défense CSRF explicite — `SameSite` seul ne suffit pas pour les mutations.

## Notes de sécurité

- **Secrets** : un secret client n'est jamais lu ni affiché. Le serveur dit
  seulement s'il en existe un. La console ne propose que de le *remplacer* :
  champ vide, aucun bouton « afficher ».
- **Autorisation** : l'interface se règle sur les **capacités** renvoyées par le
  serveur, pas sur le nom du rôle. Masquer un bouton n'est jamais une
  protection — le serveur refuse de toute façon.
- **Frontière du plan de données** : la console gère de la configuration. Audio,
  transcriptions et prompts n'y passent jamais, et aucune page n'affiche
  l'activité d'un utilisateur.
- **En-têtes web** (CSP, `frame-ancestors`, `Referrer-Policy`, `nosniff`, HSTS) :
  ils relèvent de l'hébergeur ou du reverse proxy. Vite ne les applique pas en
  production, et ce fichier ne prétend pas le contraire.
