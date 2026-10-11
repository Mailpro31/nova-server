# La transcription d'un palier materiel, par transcribe.cpp (tools/speech).
#
# Le serveur de test transcrit ainsi depuis le 11/10 : Whisper turbo GGUF Q8_0,
# 0,101 d'erreurs normalisees sur les dictees de Sash, contre 0,134 pour le
# meme turbo dans l'image faster-whisper (whisper.Dockerfile).
#
# La base est l'image vLLM du modele de texte : CUDA, nvcc, Python et FastAPI y
# sont deja, et une ecole qui telecharge les deux images ne paie ses couches
# qu'une fois. C'est aussi la base que le RTX a verifiee sur sa RTX 2060.
FROM vllm/vllm-openai:v0.28.0

# Le commit des crates transcribe-cpp 0.1.3 qu'utilise le poste Nova.
ARG TRANSCRIBE_COMMIT=a94e021ef658dc7c788837341a13f6acea3baf3c
# Turing (RTX 20), Ampere (RTX 30, A100), Ada (RTX 40), Blackwell (RTX 50).
ARG CUDA_ARCHS="75;80;86;89;120"
# Le modele GGUF, et son empreinte : une image n'est jamais construite avec un
# fichier autre que celui qui a ete mesure.
ARG GGUF_URL
ARG GGUF_SHA256

RUN pip install --no-cache-dir "cmake>=3.28,<4"
WORKDIR /src
# TRANSCRIBE_CUDA, et pas seulement GGML_CUDA : transcribe.cpp recopie ses
# options sur celles de ggml, et coupait CUDA sans prevenir (releve par le RTX).
RUN curl -fsSL "https://codeload.github.com/handy-computer/transcribe.cpp/tar.gz/${TRANSCRIBE_COMMIT}" \
      | tar xz --strip-components=1 \
 && cmake -B build -DCMAKE_BUILD_TYPE=Release -DTRANSCRIBE_BUILD_SHARED=ON \
      -DTRANSCRIBE_CUDA=ON -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHS}" \
      -DTRANSCRIBE_BUILD_TESTS=OFF -DTRANSCRIBE_BUILD_EXAMPLES=OFF \
 && cmake --build build --target transcribe -j"$(nproc)" \
 && mkdir -p /opt/transcribe/lib \
 && find build -name "*.so*" -exec cp -P {} /opt/transcribe/lib/ \; \
 && pip install --no-cache-dir --break-system-packages --no-deps /src/bindings/python \
 && cd / && rm -rf /src

RUN test -n "${GGUF_URL}" && test -n "${GGUF_SHA256}" \
 && mkdir -p /models/speech \
 && curl -fSL --retry 3 -o /models/speech/model.gguf "${GGUF_URL}" \
 && echo "${GGUF_SHA256}  /models/speech/model.gguf" | sha256sum -c -

COPY tools/speech/app.py /opt/nova/speech/app.py

# Le compose de l'installateur passe encore WHISPER__MODEL et
# WHISPER__COMPUTE_TYPE : ce service ne les lit pas. Le modele est celui de
# l'image, et l'API Nova peut l'appeler /models/speech, nom qui est ignore.
ENV LD_LIBRARY_PATH=/opt/transcribe/lib \
    TRANSCRIBE_LIBRARY=/opt/transcribe/lib/libtranscribe.so \
    SPEECH_ENGINE=transcribe_cpp \
    SPEECH_BACKEND=cuda \
    SPEECH_MODEL_PATH=/models/speech/model.gguf \
    SPEECH_DEFAULT_LANGUAGE=fr \
    HF_HUB_OFFLINE=1 \
    DO_NOT_TRACK=1 \
    PORT=8000

EXPOSE 8000
ENTRYPOINT []
CMD ["python3", "/opt/nova/speech/app.py"]

LABEL org.opencontainers.image.title="Nova Quick Lab Speech Runtime (transcribe.cpp)" \
      org.opencontainers.image.licenses="MIT AND Apache-2.0"
