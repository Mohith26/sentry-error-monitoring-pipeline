# TypeScript SDK demo app (Node 20). Builds the SDK, then runs the throwing demo.
FROM node:20-slim

WORKDIR /sdk

COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY tsconfig.json vitest.config.ts ./
COPY src/ ./src/
COPY demo/ ./demo/
COPY bench/ ./bench/

RUN npm run build

# Default: run the demo (expects SENTINEL_URL to point at the ingest API).
CMD ["node", "dist/demo/demo.js"]
