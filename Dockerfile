FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Le plan de donnees, le runtime prive, le canal de controle local et le
# Connector. Ils vivent dans la meme image parce qu'ils partagent le meme code
# et la meme version ; ce sont les `command` du compose qui decident lequel
# tourne dans quel conteneur.
# `admin.html` a disparu avec l'ancienne console (ffc812f) ; le Dockerfile ne
# l'avait pas suivi, et la construction echouait depuis. Nova Admin est une
# application separee, servie ailleurs.
# La liste suivante est la fermeture des imports de `serve.py` : elle a ete
# calculee, pas devinee. Elle etait incomplete, et l'image publiee ne pouvait
# meme pas faire `import serve` (`ModuleNotFoundError: connector_release`).
# Le conteneur `migrate` echouait pour la meme raison (`No module named
# migrations`), donc la pile ne demarrait pas du tout.
COPY main.py ai_runtime.py local_channel.py serve.py connector.py ./
# L'outil d'operateur. Il n'est demarre par aucun `command` : il est execute
# ponctuellement dans le conteneur `api`, notamment par l'installateur pour
# nommer le premier administrateur. Sans lui dans l'image, le geste decrit par
# le runbook echoue sur `can't open file '/app/admin_cli.py'`, et une
# organisation neuve n'a aucun moyen d'obtenir son premier administrateur.
COPY admin_cli.py ./
COPY connector_release.py database.py deployment.py migrations.py ./
COPY observability.py production_config.py ratelimit.py scim.py secrets_provider.py ./
# Les regles de tri, l'annuaire Google, et le processus qui le lit chaque heure.
# `main.py` importe les deux premiers au chargement : sans eux l'image se
# construit sans erreur, puis meurt au premier `import main` sur
# `ModuleNotFoundError: google_directory` -- y compris dans `prepare`, donc
# avant meme que la pile demarre. Le troisieme est le `command` du service
# `directory-sync`. `test_dockerfile_closure.py` compare desormais cette liste
# a la fermeture reelle des imports : le prochain oubli echoue en CI, pas au
# deploiement.
COPY google_directory.py group_rules.py directory_worker.py ./
# Le releve d'usage et son tour de service. `usage_worker.py` est le `command`
# du service `usage-metrics` : sans lui dans l'image, le conteneur ne demarre
# pas du tout, sur `can't open file '/app/usage_worker.py'`. La liste des
# points d'entree de `test_dockerfile_closure.py` est desormais lue dans le
# compose, donc le prochain service ajoute reclamera ses fichiers tout seul.
COPY usage_metrics.py usage_worker.py ./
COPY learning/ ./learning/
# Les fichiers SQL que `migrations.py` decouvre au runtime dans `migrations/`.
COPY migrations/ ./migrations/
# Le banc des reprises, a lancer sur le serveur avec son vrai modele :
# `docker compose exec api python scripts/bench_self_corrections.py`.
COPY scripts/bench_self_corrections.py ./scripts/
# Le banc de reformulation, avec les consignes reelles des Styles du poste :
# `docker compose exec api python scripts/bench_reformulation.py`.
COPY scripts/bench_reformulation.py scripts/bench_style_prompts.json scripts/bench_transcription.py ./scripts/

# Le mode Lab. Ces fichiers vivent dans l'image plutot que sur le poste
# client : ils heritent ainsi de la verification de digest OCI que
# l'installateur applique deja, et le Lab n'ajoute aucun telechargement.
# Rien ici n'est monte ni execute tant que le Lab n'est pas active.
COPY tools/organization-lab/lab/ /opt/nova-lab/

ENV NOVA_DB=/data/nova.db

# `serve.py`, pas `uvicorn main:app` : le serveur sert l'API publique **et** le
# canal de controle local, sur deux points d'ecoute distincts. Demarrer uvicorn
# directement ne monterait que le premier, et le Connector n'aurait aucun moyen
# d'appliquer une configuration.
CMD ["python", "serve.py"]
