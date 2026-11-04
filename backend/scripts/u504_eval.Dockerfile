FROM python:3.12.15-slim-trixie AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake libssl-dev
WORKDIR /src
COPY llama.cpp-8642541b255bfb5dc39cd351a178be09013a55d6/ ./
RUN cmake -B build -DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=ON -DLLAMA_OPENSSL=OFF -DBUILD_SHARED_LIBS=OFF && cmake --build build --target llama-server llama-cli llama-bench -j 8
FROM python:3.12.15-slim-trixie
COPY --from=build /src/build/bin/llama-server /src/build/bin/llama-cli /src/build/bin/llama-bench /usr/local/bin/
COPY --from=build /usr/lib/x86_64-linux-gnu/libgomp.so.1 /usr/lib/x86_64-linux-gnu/
ENTRYPOINT []
