# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS build
ARG MALG_VERSION=v0.0.0

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY . ./
RUN npm run build \
    && sed -i "s/__MALG_VERSION__/${MALG_VERSION}/g" /app/dist/frontend/browser/index.html \
    && test -z "$(sed -n '/__MALG_VERSION__/p' /app/dist/frontend/browser/index.html)"

FROM nginx:1.27-alpine

# The Compose service runs as the nginx account, so the image's root-only
# worker-user directive would only produce a startup warning.
RUN sed -i '/^user /d' /etc/nginx/nginx.conf

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html

EXPOSE 8080
