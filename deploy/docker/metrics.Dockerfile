# Yatra Pulse metrics consumer: Pulsar → Prometheus /metrics, SLOs, alerts, dashboard. No npm dependencies.
FROM node:22.23.2-alpine
WORKDIR /app
COPY ops/pulsar.js ops/metrics-consumer.js ops/dashboard.html ops/
ENV NODE_ENV=production PORT=8080
USER node
EXPOSE 8080
CMD ["node", "ops/metrics-consumer.js"]
