# Pinned to the exact vLLM release the RTX 2060 runtime modes were verified
# against. vllm/vllm-openai:latest moved to 0.28, whose default model runner
# requires UVA and therefore cannot start under WSL2 (Docker Desktop) at all;
# the runtime mode this image ships with is chosen by nova-llm-compat.py, and
# that decision is only meaningful against a known vLLM.
FROM vllm/vllm-openai:v0.28.0

# Le modele est un parametre de construction : un palier materiel, une image.
# Le chemin dans l'image, lui, ne varie pas -- c'est ce qui permet a la
# commande par defaut d'etre juste pour toutes les variantes. Les valeurs de
# service (longueur de contexte, part de VRAM, nom annonce) ne sont pas cuites
# ici : elles viennent du profil signe, que le compose genere transmet.
ARG LLM_MODEL=Qwen/Qwen2.5-1.5B-Instruct-AWQ
RUN python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${LLM_MODEL}', local_dir='/models/language')"

# Real-hardware compatibility probe. The installer runs this on the client GPU
# before writing any configuration and refuses to start the stack if no local
# runtime mode works. There is no remote fallback.
COPY tools/organization-lab/images/nova-llm-compat.py /opt/nova/nova-llm-compat.py

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

ENTRYPOINT ["python3", "-m", "vllm.entrypoints.openai.api_server"]
# Repli seulement : le compose genere passe toujours sa propre commande, tiree
# du profil. Sans `--served-model-name`, vLLM annoncerait le modele sous son
# chemin disque et chaque completion reviendrait en 404.
CMD ["--model", "/models/language", "--max-model-len", "2048", "--gpu-memory-utilization", "0.55"]

LABEL org.opencontainers.image.title="Nova Quick Lab Language Runtime" \
      org.opencontainers.image.licenses="Apache-2.0"
