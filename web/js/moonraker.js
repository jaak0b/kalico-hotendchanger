// Moonraker JSON-RPC 2.0 client over the websocket
export class RpcError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export class ConnectionClosed extends Error {}

// Moonraker answers an unauthorized request with HTTP status 401, which its
// JSON-RPC layer turns into code -32602 with the message "Unauthorized"
// (moonraker/common.py:434-435 and :852-853).
const UNAUTHORIZED_CODE = -32602;
const UNAUTHORIZED_MESSAGE = "Unauthorized";

export function isUnauthorized(error) {
  return (
    error instanceof RpcError &&
    error.code === UNAUTHORIZED_CODE &&
    error.message === UNAUTHORIZED_MESSAGE
  );
}

export function websocketUrl(location) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return scheme + "//" + location.host + "/websocket";
}

export class MoonrakerSocket {
  constructor(url, handlers, reconnectDelayMin, reconnectDelayMax) {
    this.url = url;
    this.handlers = handlers;
    this.reconnectDelayMin = reconnectDelayMin;
    this.reconnectDelayMax = reconnectDelayMax;
    this.delay = reconnectDelayMin;
    this.ws = null;
    this.nextId = 1;
    this.pending = new Map();
  }

  start() {
    this._open();
  }

  sessionEstablished() {
    this.delay = this.reconnectDelayMin;
  }

  _open() {
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => this.handlers.onOpen();
    ws.onmessage = (event) => this._receive(event.data);
    ws.onclose = () => {
      this.ws = null;
      this._failPending();
      this.handlers.onClose(this.delay);
      setTimeout(() => this._open(), this.delay);
      this.delay = Math.min(this.delay * 2, this.reconnectDelayMax);
    };
  }

  _failPending() {
    for (const { reject } of this.pending.values()) {
      reject(new ConnectionClosed("The connection to Moonraker closed."));
    }
    this.pending.clear();
  }

  _receive(text) {
    let message;
    try {
      message = JSON.parse(text);
    } catch (e) {
      console.error("Discarding malformed message from Moonraker:", e, text);
      return;
    }
    if (message === null || typeof message !== "object") {
      console.error("Discarding non-object message from Moonraker:", message);
      return;
    }
    if ("method" in message) {
      this.handlers.onNotification(message.method, message.params);
      return;
    }
    const entry = this.pending.get(message.id);
    if (entry === undefined) {
      console.error("Discarding response with unknown id:", message);
      return;
    }
    this.pending.delete(message.id);
    if ("error" in message) {
      const error = message.error || {};
      entry.reject(
        new RpcError(error.code, String(error.message ?? "unknown error"))
      );
    } else {
      entry.resolve(message.result);
    }
  }

  call(method, params) {
    const ws = this.ws;
    if (ws === null || ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new ConnectionClosed("Not connected to Moonraker."));
    }
    const id = this.nextId++;
    const message = { jsonrpc: "2.0", method, id };
    if (params !== undefined) {
      message.params = params;
    }
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      ws.send(JSON.stringify(message));
    });
  }
}
