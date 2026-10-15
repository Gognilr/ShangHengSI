# 前端镜像：node 构建后把静态文件放进 caddy 镜像（SPEC 20.2 的 web 服务）。
# 依赖在可联网的机器上安装，运行时不下载任何东西；node、caddy 版本固定。
FROM node:24.14.1-slim AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --include=dev
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM caddy:2.11.7
COPY --from=build /src/dist /srv/frontend
