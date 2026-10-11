# Yatra Pulse web board: static files served by unprivileged nginx on port 8080.
FROM nginxinc/nginx-unprivileged:1.29.8-alpine
COPY --chown=nginx:nginx index.html /usr/share/nginx/html/index.html
COPY --chown=nginx:nginx docs/design.html /usr/share/nginx/html/design.html
COPY --chown=nginx:nginx resources/design-discussion.html resources/architecture.html resources/thumbnail.png /usr/share/nginx/html/
EXPOSE 8080
