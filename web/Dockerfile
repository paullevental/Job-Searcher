# syntax=docker/dockerfile:1
#
# Dev-server image: Compose mounts the source and Vite serves with HMR. The
# production build (static assets behind a web server) is M11, not M0.
# The stage Compose runs is last, so a plain `docker build web/` gets it.

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
