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
COPY main.py ai_runtime.py local_channel.py serve.py connector.py ./

ENV NOVA_DB=/data/nova.db

# `serve.py`, pas `uvicorn main:app` : le serveur sert l'API publique **et** le
# canal de controle local, sur deux points d'ecoute distincts. Demarrer uvicorn
# directement ne monterait que le premier, et le Connector n'aurait aucun moyen
# d'appliquer une configuration.
CMD ["python", "serve.py"]
