# Frontend: React compiled by Node, served by nginx without root.
# The final image holds only the static files and the nginx configuration:
# no Node, no source code, no node_modules, no tests and no secrets.

# --- Stage 1: build ---------------------------------------------------------
FROM node:22.23.3-alpine AS build
WORKDIR /app

# Dependencies first: this layer is reused while package-lock.json does not change
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY index.html tsconfig.json vite.config.ts ./
COPY public ./public
COPY src ./src

# Compile-time value: Vite writes it into the JavaScript bundle. Changing it
# requires building the image again; an environment variable of the running
# container has no effect.
ARG VITE_PAYMENT_DEMO=false
RUN npm run typecheck && VITE_PAYMENT_DEMO="$VITE_PAYMENT_DEMO" npm run build

# --- Stage 2: runtime -------------------------------------------------------
# nginx as uid 101, listening on 8080 (no privileged port, no capabilities)
FROM nginxinc/nginx-unprivileged:1.28.0-alpine

ARG VITE_PAYMENT_DEMO=false
# Lets anyone check how the image was built:
#   docker image inspect ecommerce-uce/frontend --format '{{json .Config.Labels}}'
LABEL uce.payment-demo="${VITE_PAYMENT_DEMO}"

COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
