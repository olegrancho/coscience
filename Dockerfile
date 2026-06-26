FROM node:20-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[http]"
COPY --from=ui /ui/dist ./frontend/dist

ENV COSCIENCE_REPO=/data \
    COSCIENCE_HOST=0.0.0.0 \
    COSCIENCE_PORT=8000

EXPOSE 8000
CMD ["coscience-http"]
