# API Gateway: nginx with the configuration copied into the image.
# 1.28.0 or later is required: "resolve" in upstream blocks (dynamic DNS).
FROM nginx:1.28.0-alpine

# The image's sample site is replaced by the gateway configuration
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf
COPY conf.d/ /etc/nginx/conf.d/

EXPOSE 80

# nginx only: the gateway is healthy even if a service is down
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/health || exit 1
