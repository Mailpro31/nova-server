FROM fedirz/faster-whisper-server:latest-cuda

ARG WHISPER_MODEL=small
RUN uv run python -c "from faster_whisper.utils import download_model; download_model('${WHISPER_MODEL}', output_dir='/models/whisper-${WHISPER_MODEL}')"

# The model is baked in and addressed by absolute path: callers must name
# /models/whisper-small, never 'small'. A bare model name sends
# faster-whisper to huggingface_hub.snapshot_download, which both fails
# offline and would reach Hugging Face from the client machine.
#
# The analytics opt-outs stop the runtime phoning api.gradio.app on startup.
ENV HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    GRADIO_ANALYTICS_ENABLED=0 \
    DO_NOT_TRACK=1 \
    WHISPER__MODEL=/models/whisper-small \
    WHISPER__COMPUTE_TYPE=int8_float16

LABEL org.opencontainers.image.title="Nova Quick Lab Speech Runtime" \
      org.opencontainers.image.licenses="MIT AND Apache-2.0"
