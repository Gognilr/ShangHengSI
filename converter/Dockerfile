# 旧格式转换服务（SPEC 3、4、8.8，设计 docs/design/R2.md 第三节）。
# LibreOffice 只允许出现在本镜像中，不得进入 backend 镜像。
# 构建（在可联网的机器上）：
#   docker build -t aireview-converter:dev converter
# 测试（断网、只读、非 root，与 compose 中的加固条件相同）：见 tests/test_main.py 文件头。
FROM python:3.12.15-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    LANG=C.UTF-8

# 锁定版本（Q-143 ④）：从镜像源下架时改用 snapshot.debian.org 的同一版本，不跟随升级。
# 不装字体（只取文字、不排版）、不装 Java、不装 python3-uno。
ARG LO_VERSION=4:25.2.3-2+deb13u8
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui=${LO_VERSION} \
        libreoffice-calc-nogui=${LO_VERSION} \
    && rm -rf /var/lib/apt/lists/*

# converter：uid 10002、gid 10001（与 backend 的 appuser 同组，通过 /spool 交换文件）
RUN groupadd --system --gid 10001 spool \
    && useradd --system --uid 10002 --gid 10001 --no-create-home --home-dir /tmp converter

WORKDIR /srv/converter
COPY main.py ./
USER 10002:10001
CMD ["python", "/srv/converter/main.py"]
