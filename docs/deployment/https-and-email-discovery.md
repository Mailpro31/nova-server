# Exposer le serveur en HTTPS, et laisser les postes le trouver

Ce guide couvre deux choses qui vont ensemble :

1. **joindre le serveur depuis n'importe quel réseau**, sans ouvrir de port
   entrant sur le réseau de l'organisation ;
2. **laisser un membre le trouver avec sa seule adresse e-mail**, sans jamais
   taper d'adresse HTTPS.

Il s'adresse à la DSI qui héberge Nova. Il ne demande aucune modification du
code, seulement de la configuration.

---

## 1. Une seule adresse publique, par un tunnel sortant

Le serveur reste **où il est**, sur le réseau de l'organisation. Il n'a pas
besoin d'adresse publique, ni de redirection de port sur le pare-feu : un
tunnel ouvre la connexion **depuis l'intérieur** et publie une adresse HTTPS.

Exemple avec Cloudflare Tunnel :

```bash
cloudflared tunnel create nova
cloudflared tunnel route dns nova nova.example.com
cloudflared tunnel run --url http://127.0.0.1:8080 nova
```

Un reverse proxy déjà en place (nginx, Traefik, F5) fait tout aussi bien
l'affaire, du moment que le résultat est le même : une adresse HTTPS stable,
avec un certificat valide, qui aboutit sur `http://127.0.0.1:8080`.

**Pourquoi un tunnel sortant plutôt qu'un port ouvert.** Un port entrant est
une surface que quelqu'un finit par scanner ; il demande une règle de
pare-feu, une adresse publique et un certificat à renouveler. Le tunnel ne
demande rien de tout cela, et il se ferme en arrêtant un processus.

### Ce que le serveur doit savoir

| Variable | Valeur | Pourquoi |
| --- | --- | --- |
| `NOVA_SERVICE_ENDPOINT` | `https://nova.example.com` | L'adresse annoncée aux postes. Elle doit être exactement celle du tunnel. |
| `NOVA_TRUSTED_PROXIES` | l'adresse du tunnel ou du proxy | Sans elle, toutes les requêtes semblent venir du proxy : les quotas compteraient tout le monde ensemble, et le journal ne dirait plus d'où vient quoi. |
| `NOVA_ADMIN_ORIGINS` | l'origine de la console | La console n'est servie que pour les origines déclarées. |
| `NOVA_ENTRA_ALLOWED_TENANT_ID` | le tenant de l'organisation | La connexion Microsoft n'accepte alors que les comptes de l'organisation. |

Le serveur refuse une adresse de service qui ne serait pas en HTTPS, qui
porterait des identifiants, ou qui pointerait vers une adresse privée : un
poste qui suivrait une telle adresse confierait sa session à n'importe qui.

### La console d'administration

La console parle au serveur ; l'exposer au monde n'apporte rien à un membre.
Deux options, au choix de la DSI :

- la garder joignable **depuis le réseau de l'organisation seulement** ;
- la publier derrière un contrôle d'accès supplémentaire (Cloudflare Access,
  VPN, liste d'adresses).

Dans les deux cas, l'accès à Nova Admin exige déjà une connexion SSO avec
élévation.

---

## 2. Trouver l'organisation avec une adresse e-mail

Un étudiant tape `prenom.nom@example.com`. Le poste prend le domaine, lit
l'enregistrement DNS `_nova.example.com`, et y trouve l'adresse du serveur.
C'est le mécanisme de SPF et de DMARC : ce qui engage un domaine se publie
dans ce domaine.

### L'enregistrement à publier

```
_nova.example.com.   IN   TXT   "v=nova1; endpoint=https://nova.example.com"
```

Nova Admin, page **Déploiement**, affiche cet enregistrement déjà rempli pour
chaque domaine servi, avec un bouton **Vérifier** qui lit le DNS public et dit
ce qu'il y trouve : rien, une autre adresse, ou la bonne.

La lecture se fait en DNS-over-HTTPS, pas par le résolveur de la machine : un
serveur d'organisation vit souvent derrière un résolveur interne qui ne voit
pas la zone publique — celle que les postes liront.

### Ce que le poste vérifie avant de faire confiance

Un enregistrement DNS dit où aller, pas à qui l'on parle. Le poste recoupe donc
trois choses, et refuse si l'une manque :

1. l'adresse est en **HTTPS**, avec un certificat valide ;
2. son hôte appartient au **domaine de l'adresse e-mail** (`nova.example.com`
   pour `example.com`) ;
3. le serveur atteint **confirme qu'il sert ce domaine**
   (`GET /api/discovery/email-domain?domain=example.com`).

Sans la troisième, un enregistrement DNS mal publié suffirait à envoyer les
postes d'une école vers le serveur de quelqu'un d'autre.

Le serveur répond à la question posée, et à elle seule : il ne renvoie jamais
la liste de ses domaines.

### Postes gérés par la DSI

Rien à faire : la configuration machine déposée à l'installation porte déjà
l'organisation, et le parcours de découverte ne s'affiche pas.

---

## 3. Ce qu'il reste à faire une fois en place

- Déclarer l'application dans **Microsoft Entra** et restreindre au tenant de
  l'organisation.
- Vérifier depuis un réseau extérieur : l'adresse HTTPS répond, `/api/health`
  est vert, un membre se connecte avec son adresse.
- Vérifier l'enregistrement DNS depuis Nova Admin après publication : la
  propagation prend de quelques minutes à quelques heures.
