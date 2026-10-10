# Service de transcription GGUF (transcribe.cpp) au format OpenAI. Voir README.md.
#
# Le commit est celui des crates transcribe-cpp 0.1.3 qu'utilise le poste Nova
# (.cargo_vcs_info.json), compilé depuis la source officielle.
ARG BASE_DEVEL=nvidia/cuda:12.6.3-devel-ubuntu24.04
ARG BASE_RUNTIME=nvidia/cuda:12.6.3-runtime-ubuntu24.04

FROM ${BASE_DEVEL} AS build
ARG TRANSCRIBE_COMMIT=a94e021ef658dc7c788837341a13f6acea3baf3c
# 75 : Turing, la RTX 2060 du serveur de test.
ARG CUDA_ARCH=75
RUN apt-get update \
 && apt-get install -y --no-install-recommends cmake g++ make ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL "https://codeload.github.com/handy-computer/transcribe.cpp/tar.gz/${TRANSCRIBE_COMMIT}" \
    | tar xz --strip-components=1
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DTRANSCRIBE_BUILD_SHARED=ON \
      -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH} \
 && cmake --build build --target transcribe -j"$(nproc)" \
 && mkdir -p /out/lib \
 && find build -name "*.so*" -exec cp -P {} /out/lib/ \;

FROM ${BASE_RUNTIME}
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/lib/ /opt/transcribe/lib/
COPY --from=build /src/bindings/python /opt/transcribe-python
ENV LD_LIBRARY_PATH=/opt/transcribe/lib \
    TRANSCRIBE_LIBRARY=/opt/transcribe/lib/libtranscribe.so \
    SPEECH_MODEL_PATH=/models/speech.gguf \
    SPEECH_BACKEND=auto \
    SPEECH_DEFAULT_LANGUAGE=fr
RUN pip install --no-cache-dir --break-system-packages \
      fastapi==0.141.1 "uvicorn[standard]==0.52.4" python-multipart==0.0.32 \
      /opt/transcribe-python
COPY app.py /app/app.py
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["python3", "/app/app.py"]
