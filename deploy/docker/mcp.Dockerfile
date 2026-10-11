# Yatra Pulse MCP server (Streamable HTTP) with Pulsar telemetry. Listens on $PORT (Knative sets it).
FROM node:22.23.2-alpine
WORKDIR /app
COPY mcp-server/package.json mcp-server/package-lock.json mcp-server/
RUN cd mcp-server && npm ci --omit=dev && npm cache clean --force
COPY index.html ./
COPY ops/pulsar.js ops/telemetry.js ops/
COPY mcp-server/*.js mcp-server/
ENV NODE_ENV=production PORT=8080 TELEMETRY=pulsar
USER node
EXPOSE 8080
CMD ["node", "mcp-server/http.js"]
