# Nova Server — Campus

Serveur on-premise de transcription et de reformulation pour établissements.
Il fournit l'authentification Campus, les préférences d'écriture, le parcours
Compétences IA et le panneau d'administration. Les audios et textes sont
traités en mémoire ; seuls des comptes, préférences et compteurs sont stockés.

## Architecture

```text
Nova Desktop ──HTTPS──> nova-server (FastAPI)
                         ├──> Whisper (transcription)
                         └──> LLM OpenAI-compatible (reformulation)
```

## Démarrage local

Copier la configuration d'exemple et l'ajuster :

```powershell
Copy-Item .env.example .env
notepad .env
```

Pour tester l'authentification, l'admin et les préférences sans moteurs GPU :

```powershell
docker compose up -d --build --no-deps api
```

Pour tester la transcription et la reformulation avec un GPU NVIDIA :

```powershell
docker compose up -d --build
```

Sans SMTP, les codes à six chiffres sont affichés par
`docker compose logs -f api` au lieu d'être envoyés par email.

## Configuration principale

| Variable | Défaut Compose | Rôle |
|---|---|---|
| `NOVA_DOMAINS` | `eleves.ipsa.fr,ipsa.fr` | domaines email autorisés |
| `NOVA_MAX_SEATS` | `300` | nombre maximal d'utilisateurs actifs |
| `NOVA_MAX_MACHINES` | `3` | appareils actifs par compte |
| `NOVA_TOKEN_TTL_SECONDS` | `2592000` | durée d'une session en secondes |
| `NOVA_SMTP_HOST/PORT/USER/PASS/FROM` | — | envoi des codes email |
| `NOVA_ENTRA_TENANT_ID` | `organizations` | tenant Microsoft Entra ID |
| `NOVA_ENTRA_CLIENT_ID` | — | application publique Entra ; active le bouton Microsoft |

## API principale

| Route | Rôle |
|---|---|
| `GET /api/health` | santé du serveur |
| `GET /api/config` | configuration Campus publique et capacités |
| `POST /api/auth/request` | envoie ou journalise un code email |
| `POST /api/auth/verify` | échange le code contre une session |
| `POST /api/auth/entra/start` | démarre la connexion Microsoft Device Code |
| `POST /api/auth/entra/poll` | finalise Microsoft et crée la session Nova |
| `POST /api/auth/logout` | révoque la session active |
| `GET /api/me` | profil connecté |
| `POST /api/transcribe` | audio vers texte |
| `POST /api/reformulate` | texte et Style vers texte reformulé |
| `GET /api/vocabulary` | dictionnaires et snippets |
| `POST /api/dictionary` | ajoute un terme personnel |
| `POST /api/snippets` | ajoute un snippet vocal |
| `GET/POST /api/formatting-rules` | règles d'écriture |
| `POST /api/command` | transforme un texte selon une instruction |
| `POST /api/engineering-notes` | structure une note technique |
| `GET /api/ai-skills` | six modules étudiants d'usage responsable de l'IA |
| `GET /admin` | panneau d'administration web |

## Administration

L'administration passe par **Nova Admin** (`admin-web/`) : identifiant
d'organisation, connexion par le fournisseur d'identité, puis réauthentification.
Il n'y a pas de mot de passe ni de jeton d'administration partagé.

Le premier administrateur — et la récupération quand plus personne ne peut se
connecter — passe par la ligne de commande, sur le serveur :

```bash
python admin_cli.py grant <email> organization_admin
python admin_cli.py discovery enable --endpoint https://nova.exemple.fr
```

L'admin peut consulter les sièges et compteurs, gérer rôles et cohortes,
suspendre un compte et révoquer des appareils. Il n'accède pas au contenu des
dictées.

## Validation et déploiement

Le guide complet est dans [`TESTING_CAMPUS.md`](TESTING_CAMPUS.md).

```powershell
python -m pytest
```

Avant la production : placer l'API derrière HTTPS, configurer le SMTP,
restreindre l'accès réseau et sauvegarder le volume de données. Aucun secret
d'administration n'est à choisir : il n'en existe plus — l'administration
passe par Nova Admin, et son amorçage par `admin_cli.py`.

Matériel pilote conseillé : un GPU NVIDIA de 24 Go pour les services Whisper et
LLM. Sans GPU, ils peuvent être hébergés ailleurs via `NOVA_WHISPER_URL` et
`NOVA_LLM_URL`, ou Nova peut conserver son traitement local de secours.

## Runtime IA privé

Les traitements IA des membres — dictée, reformulation, Styles, commandes, AI
Skills, notes d'ingénierie — s'exécutent sur l'infrastructure de
l'organisation. Le plan de contrôle Nova conserve la configuration, les
policies et l'état de santé ; il ne voit ni audio, ni transcription, ni
requête, ni sortie de modèle.

**Aucun repli vers le cloud.** Si le runtime privé est indisponible, la requête
échoue avec un code explicite — `AI_RUNTIME_UNAVAILABLE`, `AI_RUNTIME_STARTING`,
`AI_RUNTIME_BUSY`, `AI_RUNTIME_TIMEOUT`, `AI_MODEL_NOT_READY`. Nova n'envoie
jamais silencieusement un contenu à un fournisseur d'IA externe.

Les moteurs se déclarent par la configuration du déploiement. `NOVA_LLM_URL` /
`NOVA_LLM_MODEL` et `NOVA_WHISPER_URL` / `NOVA_WHISPER_MODEL` restent acceptés ;
`NOVA_AI_TEXT_BASE_URL`, `NOVA_AI_TEXT_MODEL`, `NOVA_AI_SPEECH_BASE_URL` et
`NOVA_AI_SPEECH_MODEL` sont les noms actuels. Le serveur n'invente aucun modèle
par défaut : un déploiement qui n'en déclare pas obtient `AI_MODEL_NOT_READY`
plutôt qu'un choix silencieux. La destination doit être privée ou locale — une
adresse publique est refusée.

Le profil décrit le comportement voulu, jamais un nom de modèle. Le lien
profil → modèle appartient à la configuration du runtime.

**Efficient**, **Standard** et **Advanced** sont définis par Nova. **Custom**
ne l'est pas : ses paramètres viennent du déploiement, par
`NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS` et `NOVA_AI_CUSTOM_TEMPERATURE` — exigés
ensemble — et `NOVA_AI_CUSTOM_REASONING` en option. Tant qu'un déploiement ne
les déclare pas, Custom n'apparaît pas dans Nova Admin et l'API le refuse :
un profil sélectionnable qui se comporterait comme Standard laisserait croire
à un réglage qui ne règle rien. Ces valeurs se lisent dans la console mais ne
s'y écrivent pas — aucune URL, aucun modèle et aucun secret ne passe par cette
surface.

Nova Admin expose l'état réel sous **Deployment → AI Runtime** : matériel
détecté, profil recommandé, santé des moteurs texte et parole, configuration
souhaitée et configuration réellement appliquée. La console distingue les deux :
un réglage enregistré n'est marqué appliqué que lorsque le runtime l'a
confirmé.

## Private Connector

Pour reconfigurer un runtime privé **à distance**, l'organisation déploie le
Private Connector à côté de son nova-server. C'est lui qui appelle Nova : aucun
port entrant à ouvrir, aucune adresse privée connue de Nova.

```powershell
# Nova Admin -> Deployment -> Private Connector -> créer un code d'enrôlement
python connector.py enroll --control-plane https://nova.exemple.fr --token <code>
python connector.py run
```

Le serveur se lance avec `python serve.py` : il sert l'API publique **et** le
canal de contrôle local, sur deux points d'écoute distincts. La surface interne
du Connector n'est jamais montée sur le listener public.

En production, ce canal est un socket Unix dont les permissions font
l'authentification — il n'y a aucun secret local à gérer, faire tourner ou
oublier dans un fichier de déploiement. En développement,
`NOVA_CONNECTOR_LOCAL_TRANSPORT=loopback` utilise 127.0.0.1 avec un jeton
régénéré à chaque démarrage.

Le credential du Connector se fait tourner depuis Nova Admin, **sans
ré-enrôlement** : la console demande, le Connector conduit la bascule et prouve
qu'il sait utiliser le nouveau secret avant que l'ancien cesse de valoir. Nova
n'affiche jamais ce secret.

Le canal transporte des métadonnées de contrôle uniquement — révisions,
statuts, versions, santé. Les prompts, l'audio dicté, les transcriptions et les
sorties de modèle restent dans le plan de données de l'organisation.

Nova Admin distingue trois faits : **souhaité**, **livré** et **appliqué**. Une
révision n'est marquée appliquée que lorsque le Connector l'a confirmée.

Architecture, protocole et modèle de menace :
[`docs/architecture/private-connector.md`](docs/architecture/private-connector.md).

Sans Connector, un déploiement `dedicated` continue de se configurer lui-même :
le serveur applique directement, comme en Phase 32.

Le parcours AI Skills est activé avec `NOVA_AI_SKILLS_TRACK_PROGRESS=true`.
`NOVA_AI_SKILLS_REQUIRED=false` conserve le parcours entièrement facultatif ;
si l'établissement le rend obligatoire, Nova l'indique explicitement.
