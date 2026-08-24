# ---------------------------------------------------------------------------
# Etapa 1: build de Angular
# ---------------------------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app

# Dependencias antes del código (cache de capas)
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Token PÚBLICO de Mapbox (pk.xxx) inyectado en environment.prod.ts antes del
# build. Es visible en el bundle por diseño (como en cualquier lib JS de
# mapas); la protección real son las URL restrictions del dashboard de Mapbox.
ARG NG_MAPBOX_TOKEN=""
RUN sed -i "s|mapboxAccessToken: ''|mapboxAccessToken: '${NG_MAPBOX_TOKEN}'|" \
        src/environments/environment.prod.ts \
    && npx ng build --configuration=production

# ---------------------------------------------------------------------------
# Etapa 2: nginx sirviendo el bundle estático
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine

# Angular 17 (application builder) emite en dist/<proyecto>/browser
COPY --from=build /app/dist/smartroutes/browser /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1
