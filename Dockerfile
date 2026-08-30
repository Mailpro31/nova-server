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
COPY connector_release.py database.py deployment.py migrations.py ./
COPY observability.py production_config.py ratelimit.py scim.py secrets_provider.py ./
COPY learning/ ./learning/
# Les fichiers SQL que `migrations.py` decouvre au runtime dans `migrations/`.
COPY migrations/ ./migrations/

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
