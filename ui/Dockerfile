# ---- build ----
FROM node:22-alpine AS build
WORKDIR /app
# Next's SWC binaries are glibc-linked; on musl they need this shim.
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .

# NEXT_PUBLIC_* is inlined into the client bundle by `next build`, so these
# have to arrive as build args — setting them as Cloud Run env vars at runtime
# does nothing at all. They are branding only (no backend URL, no secret): the
# browser reaches the backend solely through the same-origin /api/backend/*
# route, which reads API_BASE_URL at RUNTIME (set it on the Cloud Run service).
# An unset ARG becomes an empty ENV and Next would inline "", so the required
# ones fail the build instead.
ARG NEXT_PUBLIC_ORG_NAME
ARG NEXT_PUBLIC_SUPPORT_EMAIL
ARG NEXT_PUBLIC_PRIVACY_URL
ARG NEXT_PUBLIC_TERMS_URL
# Optional: the nurse console's logos (paths under public/, or full URLs).
# Allowed to be empty — the console then shows its generic placeholder mark.
ARG NEXT_PUBLIC_LOGO_URL=
ARG NEXT_PUBLIC_LOGO_WIDE_URL=
# Which UI this image is: "nurse" (service voa-ui) or "admin" (service
# voa-admin). One codebase, two builds; middleware and the BFF allowlist both
# follow it (src/lib/mode.ts). Anything else fails the build below.
ARG APP_MODE=nurse
ENV NEXT_PUBLIC_APP_MODE=$APP_MODE
ENV NEXT_PUBLIC_ORG_NAME=$NEXT_PUBLIC_ORG_NAME \
    NEXT_PUBLIC_SUPPORT_EMAIL=$NEXT_PUBLIC_SUPPORT_EMAIL \
    NEXT_PUBLIC_PRIVACY_URL=$NEXT_PUBLIC_PRIVACY_URL \
    NEXT_PUBLIC_TERMS_URL=$NEXT_PUBLIC_TERMS_URL \
    NEXT_PUBLIC_LOGO_URL=$NEXT_PUBLIC_LOGO_URL \
    NEXT_PUBLIC_LOGO_WIDE_URL=$NEXT_PUBLIC_LOGO_WIDE_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN case "$NEXT_PUBLIC_APP_MODE" in \
      nurse|admin) ;; \
      *) echo "APP_MODE must be nurse or admin, got '$NEXT_PUBLIC_APP_MODE'" >&2; exit 1 ;; \
    esac
RUN for v in NEXT_PUBLIC_ORG_NAME NEXT_PUBLIC_SUPPORT_EMAIL \
             NEXT_PUBLIC_PRIVACY_URL NEXT_PUBLIC_TERMS_URL; do \
      eval "test -n \"\$$v\"" || { echo "$v build arg is empty" >&2; exit 1; }; \
    done
RUN npm run build

# ---- run ----
FROM node:22-alpine
WORKDIR /app
# API_BASE_URL (the backend's Cloud Run URL, also the ID-token audience) is
# a runtime env var on the service, not baked into the image. So are the
# optional cross-links: ADMIN_CONSOLE_URL on the nurse service and
# NURSE_CONSOLE_URL on the admin service.
ENV NODE_ENV=production PORT=8080 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
# Copied as node:node because the runtime user below has to be able to write
# .next/cache; a root-owned copy turns into a 500 on the first render that
# wants the cache.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
EXPOSE 8080
USER node
CMD ["node", "server.js"]
