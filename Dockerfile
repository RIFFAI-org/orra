FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ .
RUN npm test && npm run build && npm prune --omit=dev

FROM python:3.12-slim
COPY --from=web /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python agent.py  # offline guardrail self-check: a broken push fails the build instead of deploying
COPY --from=web /web web
# Node on $PORT is the only door in; FastAPI listens on localhost. If either process exits, the container exits and Cloud Run replaces it.
CMD ["bash", "-c", "uvicorn main:app --host 127.0.0.1 --port 8000 & (cd web && exec node server.ts) & wait -n"]
