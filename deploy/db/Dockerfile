FROM postgres:16.15

# 使用基础镜像已配置的 PGDG 源，锁定扩展包版本；保留 PG16 数据目录与入口。
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector=0.8.7-1.pgdg13+1 \
    && rm -rf /var/lib/apt/lists/*
