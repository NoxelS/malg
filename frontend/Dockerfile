# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY . ./
RUN npm run build

FROM nginx:1.27-alpine

# The Compose service runs as the nginx account, so the image's root-only
# worker-user directive would only produce a startup warning.
RUN sed -i '/^user /d' /etc/nginx/nginx.conf

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html

EXPOSE 8080
