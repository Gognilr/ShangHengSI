FROM python:3.12.15-slim-trixie AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake libssl-dev
WORKDIR /src
# 固定源码归档由离线模型目录提供，运行时不下载。
COPY vendor/llama.cpp.tar.gz /tmp/source.tar.gz
RUN echo '1688a167a8edd9499cdd0c19b8bd044f0fa184b466981977bbf2222d884953a4  /tmp/source.tar.gz' | sha256sum -c - && tar -xzf /tmp/source.tar.gz --strip-components=1
RUN cmake -B build -DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=ON -DLLAMA_OPENSSL=OFF -DBUILD_SHARED_LIBS=OFF && cmake --build build --target llama-server -j 8
FROM python:3.12.15-slim-trixie AS runtime
COPY --from=build /src/build/bin/llama-server /usr/local/bin/
COPY --from=build /usr/lib/x86_64-linux-gnu/libgomp.so.1 /usr/lib/x86_64-linux-gnu/
WORKDIR /srv/translator
COPY models/mt/ models/mt/
RUN cd models/mt && sha256sum -c MANIFEST.sha256
COPY main.py .
USER 10004:10001
CMD ["python", "main.py"]
