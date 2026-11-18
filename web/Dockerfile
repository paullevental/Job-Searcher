# syntax=docker/dockerfile:1
#
# Dev-server image; Compose bind-mounts the source for HMR. Production build is M11.
# Runtime stage must be last: `docker build` with no --target builds the final stage.

FROM node:22-alpine AS base
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN chown -R node:node /app

FROM base AS test
USER node
CMD ["npm", "run", "test"]

FROM base AS dev
# node:alpine already provides a non-root `node` user.
USER node

EXPOSE 5173

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD wget --quiet --spider http://127.0.0.1:5173/ || exit 1

CMD ["npm", "run", "dev"]
