# Nova Server — MVP

Serveur on-premise de transcription + reformulation pour établissements (écoles, entreprises).
Auth par email académique + code à 6 chiffres. Aucune donnée conservée : les audios et textes sont traités en mémoire puis oubliés.

## Architecture

```
App Nova Desktop ──HTTPS──> nova-server (ce dépôt, FastAPI)
                              ├──> whisper  (faster-whisper, GPU, transcription)
                              └──> llm      (vLLM, GPU, reformulation)
```

## Lancement (serveur avec GPU NVIDIA + Docker)

```bash
docker compose up -d
```

Sans SMTP configuré, le serveur tourne en **mode dev** : les codes à 6 chiffres
sont affichés dans les logs (`docker compose logs -f api`) au lieu d'être envoyés par email.

## Configuration (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `NOVA_DOMAINS` | `eleves.ipsa.fr,ipsa.fr` | domaines email autorisés |
| `NOVA_MAX_SEATS` | `300` | nombre max d'utilisateurs actifs (licence) |
| `NOVA_MAX_MACHINES` | `3` | appareils max par email |
| `NOVA_SMTP_HOST/PORT/USER/PASS/FROM` | — | envoi des codes (sinon : mode dev) |
| `NOVA_ADMIN_TOKEN` | généré au démarrage | clé admin (voir `/admin`) |

## API

| Route | Rôle |
|---|---|
| `POST /api/auth/request` | envoie un code à 6 chiffres si le domaine email est autorisé |
| `POST /api/auth/verify` | échange email + code contre un token durable |
| `POST /api/transcribe` | audio (multipart) → texte (Bearer token requis) |
| `POST /api/reformulate` | texte + consigne de style → texte reformulé |
| `GET /api/me` | profil de l'utilisateur connecté |
| `GET /api/admin/overview` | tableau de bord : sièges, stats du jour, tous les utilisateurs |
| `GET /api/admin/user/{email}` | profil complet : infos, appareils, usage 30 jours (compteurs) |
| `POST /api/admin/user/{email}` | modifier rôle / cohorte / note / suspension |
| `POST /api/admin/user/{email}/revoke-machines` | déconnecter tous les appareils |
| `DELETE /api/admin/user/{email}` | supprimer l'utilisateur (profil, tokens, compteurs) |
| `POST /api/admin/cohort/{cohort}/disable` | suspendre toute une cohorte (fin d'année) |
| `GET /admin` | **panneau d'administration web** (token admin requis) |

## Panneau admin

Ouvrir `http://serveur:8080/admin`, entrer le `NOVA_ADMIN_TOKEN`.
L'admin peut : voir chaque étudiant (email, rôle, cohorte, appareils, dernière
activité, compteurs d'usage), modifier son rôle/cohorte/note, suspendre ou
réactiver un compte, déconnecter ses appareils, le supprimer, et suspendre une
cohorte entière en fin d'année. **Confidentialité : seuls des compteurs sont
stockés — jamais le contenu des dictées.**

## Sécurité (à faire avant la production)

- [ ] Mettre du HTTPS devant (reverse proxy Caddy/nginx avec le certificat de l'établissement)
- [ ] Configurer le SMTP de l'établissement
- [ ] Définir `NOVA_ADMIN_TOKEN` manuellement et le garder secret
- [ ] Ne jamais exposer le port 8080 sur Internet : réseau interne ou VPN uniquement

## Matériel conseillé

- 1 GPU 24 Go (RTX 3090/4090) → ~30-50 dictées simultanées, largement suffisant pour un pilote
- Sans GPU : retirer les services `whisper`/`llm`, brancher `NOVA_WHISPER_URL`/`NOVA_LLM_URL` sur des services externes — ou laisser l'app en mode local (fallback)
