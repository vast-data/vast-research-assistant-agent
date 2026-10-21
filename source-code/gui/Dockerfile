# Stage 1: Build the Angular application
# Build context should be the gui/ directory
ARG SOURCE_REGISTRY=""
FROM ${SOURCE_REGISTRY}docker.io/library/node:20-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm install --legacy-peer-deps

COPY . .

ENV BASE_API_URL=http://research-assistant:8000
RUN npm run generate-prod-env
RUN npm run build-prod

# Stage 2: Unprivileged nginx (PSS / random non-root UID)
FROM ${SOURCE_REGISTRY}docker.io/library/nginx:1.27.5-alpine

RUN rm -rf /usr/share/nginx/html/*

COPY --from=build /gui_dist /usr/share/nginx/html

# Main config: no `user` directive, pid/temp under /tmp
COPY nginx-main.conf /etc/nginx/nginx.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY docker-entrypoint.sh /custom-entrypoint.sh
RUN chmod +x /custom-entrypoint.sh \
    && mkdir -p /tmp/nginx_proxy /tmp/nginx_client /tmp/nginx_fastcgi /tmp/nginx_uwsgi /tmp/nginx_scgi \
    && chmod -R 777 /tmp \
    # Ensure stock "user nginx;" never comes back if base image layers change
    && grep -q 'pid /tmp/nginx.pid' /etc/nginx/nginx.conf \
    && ! grep -qE '^\s*user\s+' /etc/nginx/nginx.conf

# Numeric UID so PSS runAsNonRoot can verify; nginx must NOT run as root
# (as root it tries chown temp dirs to 101 and fails when CAP_CHOWN is dropped).
USER 101

EXPOSE 8080

ENTRYPOINT ["/custom-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
