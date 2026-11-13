# Code d'activation à usage unique

Au lieu d'envoyer à la DSI une longue licence à copier, Sash lui donne un code
court (`NOVA-7K2P-91XQ`). Le serveur de l'école l'échange une fois contre la
licence signée, puis le code est invalidé.

## Ce qui est où

| Élément | Où | Contenu |
|---|---|---|
| Clé privée de licence | PC de Sash uniquement | jamais envoyée, jamais sur Cloudflare |
| Service d'activation (`activation/`) | Cloudflare Workers + D1 | pour chaque code : empreinte SHA-256, **licence déjà signée**, école, date d'expiration |
| Serveur de l'école | chez l'école | `NOVA_ACTIVATION_CODE`, `NOVA_ACTIVATION_URL` ; la licence reçue est vérifiée avec la clé publique intégrée (`licence.py`) |

Le service ne peut donc pas fabriquer de licence : s'il était compromis, il
pourrait au pire refuser des codes ou rendre des licences déjà émises.

Garanties : code stocké haché, jamais en clair ; usage unique atomique
(`UPDATE … WHERE used_at IS NULL`) ; expire au bout de 30 jours par défaut ;
10 essais par minute et par adresse ; même réponse (403) pour un code inconnu,
utilisé ou expiré ; jeton d'administration comparé en temps constant.

## Mise en ligne (une fois, par Sash)

Prérequis : un compte Cloudflare, Node 20+.

```bash
cd activation
npm install
npx wrangler login
npx wrangler d1 create nova-activation        # note le database_id affiché
# colle-le dans wrangler.toml (REPLACE_WITH_D1_DATABASE_ID)
npx wrangler d1 execute nova-activation --remote --file=schema.sql
npx wrangler secret put ADMIN_TOKEN           # saisis un jeton long et aléatoire
npx wrangler deploy                           # affiche l'adresse https://nova-activation.<compte>.workers.dev
```

Génère le jeton avec un gestionnaire de mots de passe (32 caractères
aléatoires au moins) ; ne le mets ni dans le dépôt ni dans une commande
visible. Tests du service : `cd activation && npm test`.

## Parcours Sash → DSI

1. Sur le PC de Sash, le jeton dans un fichier hors dépôt, puis :

   ```bash
   export NOVA_ACTIVATION_URL=https://nova-activation.<compte>.workers.dev
   export NOVA_ACTIVATION_ADMIN_TOKEN_FILE=C:/chemin/nova-activation-admin.token
   python licence.py activation-code C:/chemin/nova-licence.key \
       --organization IPSA --tier 151-250 --kind subscription \
       --starts 2026-09-01 --ends 2027-08-31
   ```

   La licence est signée sur place ; seuls l'empreinte du code et la licence
   signée partent au service. Le code s'affiche une seule fois :
   `NOVA-7K2P-91XQ` (valable 30 jours, `--code-days` pour changer).
2. Sash transmet le code à la DSI (message ou appel).
3. La DSI renseigne dans l'environnement du serveur (`.env`) — l'adresse du service est déjà la valeur par défaut :

   ```
   NOVA_ACTIVATION_CODE=NOVA-7K2P-91XQ
   ```

   puis redémarre le serveur. Au démarrage il échange le code, vérifie la
   signature et garde la licence dans `licence-activation.txt`, à côté de la
   base (volume `/data`). La DSI peut ensuite retirer `NOVA_ACTIVATION_CODE`.
4. Vérification : `/api/metrics` (série `nova_licence_info`) ou la console
   d'administration affichent le palier et l'échéance.

## Cas particuliers

- `NOVA_LICENCE` posée : elle reste prioritaire, aucun échange n'a lieu.
- Redémarrages : la licence enregistrée suffit, le code n'est plus utilisé.
- Code refusé (déjà utilisé, expiré, faute de frappe) : le serveur démarre
  sans licence, comme avant, et l'écrit dans ses journaux. Sash génère un
  nouveau code ; un ancien code non utilisé peut rester, il expirera.
- Renouvellement : nouvelle licence, nouveau code ; supprimer
  `licence-activation.txt` pour que le serveur échange le nouveau code.
- Fuite du jeton d'administration : `wrangler secret put ADMIN_TOKEN` avec une
  nouvelle valeur ; les licences déjà émises ne sont pas touchées.

## Console en ligne (nova-console)

Créer un code depuis le navigateur, sans le PC de l'éditeur : https://nova-console.nova-ecole.workers.dev.

- Signe avec une **seconde clé**, gardée comme secret Cloudflare (`SIGNING_KEY`). Sa clé publique est `ONLINE_PUBLIC_KEY_PEM` dans `licence.py` ; la clé hors ligne reste valable, en secours. Si la clé en ligne fuit : la retirer de `licence.py`, publier le serveur, en créer une autre.
- Protégée par Cloudflare Access (code envoyé à l'adresse de l'éditeur). Le worker revérifie le jeton d'Access (`ACCESS_TEAM_DOMAIN`, `ACCESS_AUD`) : vides, il refuse tout.
- Même base D1 que le service d'activation ; la liste montre l'établissement, l'offre, les dates et si le code a servi. Le code n'est affiché qu'une fois, jamais stocké en clair.
- Déploiement : `cd activation && npx wrangler deploy -c wrangler.console.toml`.

`NOVA_ACTIVATION_URL` a désormais pour valeur par défaut l'adresse du service de l'éditeur : l'école ne pose que `NOVA_ACTIVATION_CODE`.
