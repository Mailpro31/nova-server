FROM fedirz/faster-whisper-server:latest-cuda

# Un palier materiel, une image. Le type de calcul est declare ici parce que
# `faster-whisper` le lit dans l'environnement, mais le profil signe le
# retransmet a l'execution : c'est lui qui fait foi.
ARG WHISPER_MODEL=small
ARG WHISPER_COMPUTE_TYPE=int8_float16
RUN uv run python -c "from faster_whisper.utils import download_model; download_model('${WHISPER_MODEL}', output_dir='/models/speech')"

# The model is baked in and addressed by absolute path: callers must name
# /models/speech, never 'small'. A bare model name sends
# faster-whisper to huggingface_hub.snapshot_download, which both fails
# offline and would reach Hugging Face from the client machine.
#
# The analytics opt-outs stop the runtime phoning api.gradio.app on startup.
ENV HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    GRADIO_ANALYTICS_ENABLED=0 \
    DO_NOT_TRACK=1 \
    WHISPER__MODEL=/models/speech \
    WHISPER__COMPUTE_TYPE=${WHISPER_COMPUTE_TYPE}

LABEL org.opencontainers.image.title="Nova Quick Lab Speech Runtime" \
      org.opencontainers.image.licenses="MIT AND Apache-2.0"
