# Transcription GGUF (transcribe.cpp)

Un service de transcription qui parle comme `faster-whisper-server`, pour
utiliser un modèle GGUF de transcribe.cpp (Cohere Transcribe, Canary,
Parakeet…) sans changer le code du serveur Nova.

## Pourquoi

Banc v2 du RTX, le 11/10, sur 21 dictées de Sash (voix réelle, vocabulaire de
cours) :

| moteur | erreurs normalisées | mots critiques justes |
|---|---|---|
| Cohere Transcribe 03-2026 Q5_K_M | 8,9 % | 67/71 |
| Canary-1B-v2 Q8_0 | 12,1 % | 61/71 |
| Whisper large-v3-turbo du service | 23,5 % | 51/71 |

Canary a inventé une phrase sur un simple « euh ». Cohere, non.

## Brancher

1. Construire l'image : `docker build -t nova-local/speech-gguf tools/speech-gguf`.
   Pour une autre carte, passer `--build-arg CUDA_ARCH=…`.
2. Monter le modèle en `/models/speech.gguf`.
3. Dans la configuration du serveur Nova :
   - `NOVA_WHISPER_URL=http://speech-gguf:8000`
   - `NOVA_WHISPER_MODEL=cohere-transcribe-03-2026`, un nom lisible, pour la console.
4. **Mémoire graphique** : sur la RTX 2060 (6 Go), Cohere Q5 prend environ
   1,8 Go. Il **remplace** le conteneur `whisper` ; il ne peut pas tourner à
   côté de lui et de vLLM.

## Ce qui change pour le serveur Nova

- Même route (`POST /v1/audio/transcriptions`), mêmes champs, même santé (`GET /health`).
- Le français est imposé quand le poste ne donne pas de langue (`SPEECH_DEFAULT_LANGUAGE`).
- Un audio illisible reçoit 400, que le serveur rend en `INVALID_AUDIO`.
- **L'amorce de lexique (`prompt`) n'est pas transmise** : Cohere n'en a pas. Sur le banc v2, le lexique n'aidait presque pas le turbo (0,258 → 0,250).
- **Pas de confiance par passage** (`avg_logprob`). La détection « dictée
  incomprise » ne s'appuie alors que sur les boucles et les crédits de
  sous-titres. Les silences et les bruits doivent donc être vérifiés au banc
  avant la bascule.

Tests : `test_speech_gguf.py`, à la racine du dépôt.
