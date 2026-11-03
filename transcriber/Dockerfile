FROM python:3.12.15-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /srv/transcriber
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --system --gid 10001 spool && useradd --system --uid 10003 --gid 10001 --no-create-home transcriber
COPY main.py inference.py vad_postprocessor.py ./

FROM base AS test
RUN touch /srv/transcriber/TEST_IMAGE
USER 10003:10001
CMD ["python", "main.py", "--fake"]

FROM base AS runtime
COPY models/asr/ ./models/asr/
RUN cd models/asr && sha256sum -c MANIFEST.sha256
USER 10003:10001
CMD ["python", "main.py"]
