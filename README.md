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

Copier la configuration d'exemple et choisir un jeton administrateur :

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
| `NOVA_ADMIN_TOKEN` | `nova-admin-local` | clé admin locale, à remplacer avant déploiement |
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

Ouvrir `http://localhost:8080/admin`, puis entrer la valeur
`NOVA_ADMIN_TOKEN` du fichier `.env`. Pour un test local avec la configuration
d'exemple, utiliser `nova-admin-local`.

L'admin peut consulter les sièges et compteurs, gérer rôles et cohortes,
suspendre un compte et révoquer des appareils. Il n'accède pas au contenu des
dictées.

## Validation et déploiement

Le guide complet est dans [`TESTING_CAMPUS.md`](TESTING_CAMPUS.md).

```powershell
python -m unittest -v test_main.py
```

Avant la production : placer l'API derrière HTTPS, configurer le SMTP, choisir
un secret admin long, restreindre l'accès réseau et sauvegarder le volume de
données.

Matériel pilote conseillé : un GPU NVIDIA de 24 Go pour les services Whisper et
LLM. Sans GPU, ils peuvent être hébergés ailleurs via `NOVA_WHISPER_URL` et
`NOVA_LLM_URL`, ou Nova peut conserver son traitement local de secours.

Le parcours AI Skills est activé avec `NOVA_AI_SKILLS_TRACK_PROGRESS=true`.
`NOVA_AI_SKILLS_REQUIRED=false` conserve le parcours entièrement facultatif ;
si l'établissement le rend obligatoire, Nova l'indique explicitement.
