# 后端镜像（app 与 worker 共用）。基础镜像固定小版本（SPEC 第 3 节）。
# 构建（在可联网的机器上）：
#   docker build -t aireview-backend:dev --target runtime backend
#   docker build -t aireview-backend-test --target test backend
# 测试：
#   docker run --rm aireview-backend-test
FROM python:3.12.15-slim-trixie AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

# postgresql-client 16（备份用 pg_dump）：Debian trixie 自带的是 17，需用 PostgreSQL 官方源。
# libgl1 / libglib2.0-0：RapidOCR 依赖的 opencv 运行库。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg libgl1 libglib2.0-0 \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/backend
COPY requirements.txt requirements-dev.txt ./

FROM base AS runtime
# appuser 固定 uid=10001、gid=10001：宿主上的组 aireview（gid 10001）可读备份文件（Q-120）
RUN pip install -r requirements.txt && pip check \
    && groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --no-create-home appuser \
    && mkdir -p /data/files /backup && chown appuser:appuser /data/files /backup
COPY app ./app
COPY alembic.ini ./
COPY resources ./resources
RUN python -c "from pathlib import Path; import rapidocr_onnxruntime as r; assert len(list((Path(r.__file__).parent / 'models').glob('*.onnx'))) == 3"
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]

FROM base AS test
# fonts-wqy-microhei：只用于生成中文测试样本（tests/fixtures），运行镜像不含。
RUN apt-get update     && apt-get install -y --no-install-recommends fonts-wqy-microhei     && rm -rf /var/lib/apt/lists/*
RUN pip install -r requirements-dev.txt && pip check
COPY app ./app
COPY alembic.ini ./
COPY resources ./resources
COPY tests ./tests
COPY pytest.ini ./
CMD ["pytest"]
