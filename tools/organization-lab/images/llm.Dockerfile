FROM vllm/vllm-openai:latest

ARG LLM_MODEL=Qwen/Qwen2.5-1.5B-Instruct-AWQ
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('${LLM_MODEL}', local_dir='/models/qwen2.5-1.5b-instruct-awq')"

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

ENTRYPOINT ["python3", "-m", "vllm.entrypoints.openai.api_server"]
CMD ["--model", "/models/qwen2.5-1.5b-instruct-awq", "--served-model-name", "Qwen/Qwen2.5-1.5B-Instruct-AWQ", "--max-model-len", "2048", "--gpu-memory-utilization", "0.55"]

LABEL org.opencontainers.image.title="Nova Quick Lab Language Runtime" \
      org.opencontainers.image.licenses="Apache-2.0"
