# Procédure vacances : surveiller le serveur Nova (DSI)

Nova est un service de dictée pour les élèves. Le serveur tourne en conteneurs
Docker, décrits par `docker-compose.yml`. Toutes les commandes se lancent **dans
le dossier `nova-server` du serveur** (celui qui contient `docker-compose.yml`).

| Service | Rôle |
| --- | --- |
| `api` | l'API que les postes appellent (port **8080** sur la machine) |
| `whisper` ou `speech-gguf` | la transcription de la voix (carte graphique) |
| `llm` | la mise au propre du texte (carte graphique) |
| `usage-metrics` | relevé d'usage, en arrière-plan |
| `prepare` | prépare la base puis s'arrête : **« Exited (0) » est normal** |

Selon l'installation, d'autres services peuvent tourner (`alloy`, `connector`,
`voice`, `directory-sync`). Si le dossier utilise `docker-compose.production.yml`
plutôt que `docker-compose.yml`, ajouter `--file docker-compose.production.yml`
juste après `docker compose` dans chaque commande. La référence est ce que montre `docker compose ps`.

## 1. Vérifier que le service marche

```
docker compose ps
curl http://localhost:8080/api/livez     # {"status":"ok"} : le processus vit
curl http://localhost:8080/api/health    # {"status":"ok", ..., "version": ...}
curl http://localhost:8080/api/readyz    # 200 : prêt ; 503 : base, schéma ou compteur
```

`/api/readyz` est indicatif : un 503 seul, avec `/api/health` à `"ok"`, ne
justifie pas un appel.

Sous PowerShell, écrire `curl.exe`. Tout va bien si les services sont « running »
(ou « Up ») et si `/api/health` répond `"status":"ok"`.

## 2. Redémarrer proprement

Redémarrer seulement le service en cause, en le nommant :

```
docker compose restart api
docker compose restart speech-gguf      # ou whisper, ou llm
```

Puis refaire le point 1. Au démarrage, la transcription peut mettre quelques
minutes à charger son modèle : attendre avant de conclure. Si un redémarrage ne
suffit pas, appeler Sash (point 5) : ne pas lancer `docker compose down` ni
`docker compose up`, qui ne relancent pas les mêmes services que l'installation.

**La machine a redémarré** (coupure de courant, mise à jour Windows) : `api`,
`llm` et la transcription ne repartent pas seuls. Lister les conteneurs, puis
démarrer ceux qui sont arrêtés (« Exited »), en les nommant :

```
docker compose ps -a
docker compose start api llm speech-gguf
```

Si `speech-gguf` figure dans la liste, démarrer `speech-gguf` et **jamais**
`whisper` (les deux ensemble saturent la carte graphique). Sinon, démarrer
`whisper` à sa place. `prepare` arrêté est normal. Puis refaire le point 1.

## 3. Regarder les journaux

```
docker compose logs --tail 200 api
docker compose logs -f api              # en direct ; Ctrl+C pour sortir
```

Les journaux de `api` sont caviardés : ni texte dicté ni mot de passe. Ceux des
autres services (`llm`, `speech-gguf`, `whisper`) ne le sont pas forcément : les
lire sur place, et ne les envoyer à Sash que s'il les demande.

## 4. Espace disque et mémoire de la carte graphique

```
docker system df          # place prise par Docker (lecture seule)
nvidia-smi                # mémoire de la carte graphique (VRAM) et processus
```

Disque plein ou VRAM pleine : ne rien effacer, appeler Sash (point 5).

## 5. Quand appeler Sash

Sash reste joignable à distance. L'appeler si :

- `/api/health` ne répond pas, ou un service reste arrêté, **après un
  redémarrage** (point 2) ;
- un service redémarre en boucle (« Restarting » dans `docker compose ps`) ;
- le disque ou la VRAM est plein ;
- les élèves signalent que la dictée échoue alors que tout paraît « ok ».

Lui envoyer, en texte copié :

1. la sortie de `docker compose ps` ;
2. la réponse de `curl http://localhost:8080/api/health` ;
3. `docker compose logs --tail 200 api` (les autres services seulement s'il
   les demande) ;
4. la sortie de `nvidia-smi` et de `docker system df` ;
5. l'heure du début du problème et ce qui a déjà été tenté.

## 6. Ce qu'il ne faut JAMAIS faire

- **Supprimer les volumes** : ni `docker compose down -v`, ni
  `docker volume rm`, ni `docker volume prune`, ni `docker system prune`. Le
  volume `nova-data` contient les comptes de l'école, `nova-models` les modèles.
- **Ouvrir, modifier ou copier le fichier `.env`**, ni le dossier `secrets` : ils
  contiennent les clés du service. Ne jamais les envoyer à qui que ce soit.
- **Mettre à jour les modèles ou les images** : ni `docker compose pull`, ni
  `docker compose up --build`, ni changement de modèle. Une mise à jour peut
  saturer la carte graphique ou changer la qualité de la dictée.
- Lancer d'autres commandes Nova (`admin_cli.py`, migrations, sauvegarde,
  restauration) sans Sash.
