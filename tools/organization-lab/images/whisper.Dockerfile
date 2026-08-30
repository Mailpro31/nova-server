FROM fedirz/faster-whisper-server:latest-cuda

ARG WHISPER_MODEL=small
RUN python -c "from faster_whisper.utils import download_model; download_model('${WHISPER_MODEL}', output_dir='/models/whisper-${WHISPER_MODEL}')"

ENV HF_HUB_OFFLINE=1 \
    WHISPER__MODEL=/models/whisper-small \
    WHISPER__COMPUTE_TYPE=int8_float16

LABEL org.opencontainers.image.title="Nova Quick Lab Speech Runtime" \
      org.opencontainers.image.licenses="MIT AND Apache-2.0"
