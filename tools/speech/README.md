# Service de transcription

Un service de transcription qui parle comme `faster-whisper-server` : le
serveur Nova l'appelle sans changer de code. Seuls `NOVA_WHISPER_URL` et
`NOVA_WHISPER_MODEL` changent.

Deux moteurs, au choix (`SPEECH_ENGINE`) :

| moteur | modèles | image |
|---|---|---|
| `faster_whisper` | Whisper : turbo, large-v3, large-v3-french… | `Dockerfile.whisper` |
| `transcribe_cpp` | GGUF : Cohere Transcribe, Canary… | `Dockerfile` |

## Pourquoi

Banc v2 du RTX, le 11/10 : 21 dictées de Sash, voix réelle, vocabulaire de cours.

| moteur | erreurs normalisées | mots critiques justes |
|---|---|---|
| Cohere Transcribe 03-2026 Q5_K_M | 8,9 % | 67/71 |
| whisper-large-v3-french | 10,5 % | 63/71 |
| turbo, faisceau 5 et repli de température | 11,7 % | 62/71 |
| Canary-1B-v2 Q8_0 | 12,1 % | 61/71 |
| **turbo derrière faster-whisper-server (le service jusqu'ici)** | **23,5 %** | **51/71** |

Le même modèle turbo fait deux fois plus d'erreurs derrière
`faster-whisper-server`. Ce serveur fixe la température à 0 : un passage raté
n'est jamais retenté. Le moteur `faster_whisper` de ce service garde les
réglages par défaut de faster-whisper, c'est-à-dire un faisceau de 5 et un
repli de température.

Inventions sur des silences et des bruits :
- les Whisper n'en ont fait aucune ;
- Canary en a fait 2 sur 6 ;
- Cohere en a fait 5 sur 6. Le filtre de crédits de sous-titres et la porte de silence du serveur les arrêtent.

## Brancher

### Whisper bien décodé (le moins risqué)

1. `docker build -t nova-local/speech-whisper -f tools/speech/Dockerfile.whisper tools/speech`
2. Le service remplace le conteneur `whisper`. Réglages :
   - `SPEECH_MODEL` : `large-v3-turbo`, ou le chemin d'un modèle CTranslate2 ;
   - `SPEECH_COMPUTE_TYPE=int8_float16` ;
   - le volume des modèles, comme pour `whisper`.
3. Côté Nova : `NOVA_WHISPER_URL=http://speech:8000`.

### Un modèle GGUF

1. `docker build -t nova-local/speech-gguf tools/speech`. Pour une autre carte : `--build-arg CUDA_ARCH=…`.
2. Monter le modèle en `/models/speech.gguf`.
3. Côté Nova : `NOVA_WHISPER_URL=http://speech:8000` et `NOVA_WHISPER_MODEL=cohere-transcribe-03-2026`.
4. Mémoire graphique : Cohere Q5 prend environ 1,8 Go. Sur la RTX 2060, il **remplace** Whisper.

## Ce que le serveur Nova reçoit

- Même route (`POST /v1/audio/transcriptions`), mêmes champs, même santé (`GET /health`).
- Le français est imposé quand le poste ne donne pas de langue (`SPEECH_DEFAULT_LANGUAGE`).
- Un audio illisible reçoit 400, que Nova rend en `INVALID_AUDIO`.
- L'amorce de lexique (`prompt`) n'est pas transmise : mesurée, elle dégradait les deux Whisper. Nova applique le vocabulaire après la transcription.
- La confiance par passage (`avg_logprob`) est rendue avec Whisper, pas avec les modèles GGUF.

Tests : `test_speech_service.py`, à la racine du dépôt.
