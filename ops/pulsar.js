// Minimal Apache Pulsar client over Pulsar's WebSocket API, using Node's built-in WebSocket
// (Node 22+), so there are no native dependencies.
//   Producer: ws://<host>:8080/ws/v2/producer/persistent/<tenant>/<namespace>/<topic>
//   Consumer: ws://<host>:8080/ws/v2/consumer/persistent/<tenant>/<namespace>/<topic>/<subscription>
// Configure with PULSAR_WS_URL (default ws://localhost:8080/ws/v2) and PULSAR_NAMESPACE (default public/default).

export const PULSAR_WS_URL = process.env.PULSAR_WS_URL || "ws://localhost:8080/ws/v2";
export const PULSAR_NAMESPACE = process.env.PULSAR_NAMESPACE || "public/default";
export const TOPICS = {
  telemetry: "yatra-telemetry",    // one event per MCP tool call, server start/stop
  eval: "yatra-eval",              // one event per evaluator run
  alerts: "yatra-alerts",          // SLO breaches raised by the metrics consumer
  dlq: "yatra-telemetry-dlq",      // telemetry the consumer could not parse
};

const topicPath = (topic) => `persistent/${PULSAR_NAMESPACE}/${topic}`;
const b64 = (obj) => Buffer.from(JSON.stringify(obj)).toString("base64");
const unb64 = (s) => JSON.parse(Buffer.from(s, "base64").toString("utf8"));
const backoff = (attempt) => Math.min(30000, 500 * 2 ** attempt);

/** Producer with reconnect. send() never throws; it reports false when not connected so callers can fall back. */
export class Producer {
  constructor(topic, { onState = () => {} } = {}) {
    this.topic = topic;
    this.url = `${PULSAR_WS_URL}/producer/${topicPath(topic)}`;
    this.onState = onState;
    this.pending = new Map();
    this.seq = 0;
    this.attempt = 0;
    this.closed = false;
    this.stats = { sent: 0, acked: 0, failed: 0 };
    this.connect();
  }

  get connected() { return this.ws?.readyState === WebSocket.OPEN; }

  connect() {
    if (this.closed) return;
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => { this.attempt = 0; this.onState("connected"); };
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(String(e.data)); } catch { return; }
      const p = this.pending.get(msg.context);
      if (!p) return;
      this.pending.delete(msg.context);
      clearTimeout(p.timer);
      if (msg.result === "ok") { this.stats.acked++; p.resolve(msg.messageId); }
      else { this.stats.failed++; p.reject(new Error(msg.errorMsg || msg.result)); }
    };
    ws.onerror = () => {};
    ws.onclose = () => {
      for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(new Error("connection closed")); }
      this.pending.clear();
      if (this.closed) return;
      this.onState("disconnected");
      setTimeout(() => this.connect(), backoff(this.attempt++)).unref?.();
    };
  }

  /** Fire and forget. Returns false if not connected (the caller decides where the event goes instead). */
  send(obj, properties = {}) {
    if (!this.connected) return false;
    const context = String(++this.seq);
    this.ws.send(JSON.stringify({ payload: b64(obj), properties, context }));
    this.stats.sent++;
    this.pending.set(context, { resolve: () => {}, reject: () => {}, timer: setTimeout(() => this.pending.delete(context), 10000) });
    return true;
  }

  /** Send and wait for the broker's ack. Waits up to timeoutMs for a connection first. */
  async sendAndWait(obj, properties = {}, timeoutMs = 8000) {
    const until = Date.now() + timeoutMs;
    while (!this.connected && Date.now() < until) await new Promise((r) => setTimeout(r, 100));
    if (!this.connected) throw new Error(`Pulsar not reachable at ${PULSAR_WS_URL}`);
    const context = String(++this.seq);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(context); reject(new Error("ack timeout")); }, Math.max(1000, until - Date.now()));
      this.pending.set(context, { resolve, reject, timer });
      this.ws.send(JSON.stringify({ payload: b64(obj), properties, context }));
      this.stats.sent++;
    });
  }

  async close(drainMs = 1500) {
    const until = Date.now() + drainMs;
    while (this.pending.size && Date.now() < until) await new Promise((r) => setTimeout(r, 50));
    this.closed = true;
    try { this.ws?.close(); } catch {}
  }
}

/**
 * Shared-subscription consumer with reconnect. onMessage(obj, meta) may be async; the message is
 * acknowledged when it resolves and negatively acknowledged (redelivered) when it throws.
 * Payloads that aren't JSON are passed to onPoison(rawText, meta) and acknowledged.
 */
export function consume(topic, subscription, onMessage, { onState = () => {}, onPoison = () => {}, subscriptionType = "Shared" } = {}) {
  const url = `${PULSAR_WS_URL}/consumer/${topicPath(topic)}/${subscription}?subscriptionType=${subscriptionType}&receiverQueueSize=500`;
  let ws, attempt = 0, closed = false;
  const state = { connected: false, received: 0, acked: 0, nacked: 0, poison: 0, lastMessageAt: null };

  const connect = () => {
    if (closed) return;
    ws = new WebSocket(url);
    ws.onopen = () => { attempt = 0; state.connected = true; onState("connected"); };
    ws.onmessage = async (e) => {
      let frame;
      try { frame = JSON.parse(String(e.data)); } catch { return; }
      if (!frame.messageId) return;
      state.received++;
      state.lastMessageAt = Date.now();
      const meta = { messageId: frame.messageId, publishTime: frame.publishTime, properties: frame.properties || {} };
      let obj;
      try { obj = unb64(frame.payload); }
      catch {
        state.poison++;
        try { onPoison(Buffer.from(frame.payload || "", "base64").toString("utf8"), meta); } catch {}
        ws.send(JSON.stringify({ messageId: frame.messageId }));
        return;
      }
      try {
        await onMessage(obj, meta);
        ws.send(JSON.stringify({ messageId: frame.messageId }));
        state.acked++;
      } catch {
        ws.send(JSON.stringify({ type: "negativeAcknowledge", messageId: frame.messageId }));
        state.nacked++;
      }
    };
    ws.onerror = () => {};
    ws.onclose = () => {
      state.connected = false;
      if (closed) return;
      onState("disconnected");
      setTimeout(connect, backoff(attempt++));
    };
  };
  connect();
  return { state, close() { closed = true; try { ws?.close(); } catch {} } };
}
