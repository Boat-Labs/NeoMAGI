import type { RPCRequest, ServerMessage, ConnectionStatus } from "@/types/rpc"

export interface WebSocketClientOptions {
  url: string
  onMessage: (message: ServerMessage) => void
  onStatusChange: (status: ConnectionStatus) => void
  onConnected?: () => void
  onAuthFailed?: () => void
  authToken?: string | null
  reconnect?: boolean
  baseReconnectMs?: number
  maxReconnectMs?: number
  maxReconnectAttempts?: number
}

const DEFAULTS = {
  reconnect: true,
  baseReconnectMs: 1000,
  maxReconnectMs: 16000,
  maxReconnectAttempts: Infinity,
} as const

export class WebSocketClient {
  private ws: WebSocket | null = null
  private options: Required<Omit<WebSocketClientOptions, "onConnected" | "onAuthFailed" | "authToken">> & {
    onConnected?: () => void
    onAuthFailed?: () => void
    authToken?: string | null
  }
  private reconnectAttempts = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private intentionalClose = false
  private pendingAuth = false

  constructor(options: WebSocketClientOptions) {
    this.options = { ...DEFAULTS, ...options }
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) {
      return
    }

    this.intentionalClose = false
    this.options.onStatusChange("connecting")

    try {
      this.ws = new WebSocket(this.options.url)
    } catch {
      console.error("[WS] Failed to create WebSocket")
      this.attemptReconnect()
      return
    }

    this.ws.onopen = () => {
      console.log("[WS] Connected to", this.options.url)
      this.reconnectAttempts = 0

      if (this.options.authToken) {
        // Auth mode: send auth RPC, wait for response before onConnected
        this.pendingAuth = true
        this.send({
          type: "request",
          id: "auth-handshake",
          method: "auth",
          params: { token: this.options.authToken },
        })
      } else {
        // No-auth mode: immediately ready
        this.options.onStatusChange("connected")
        this.options.onConnected?.()
      }
    }

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const data: unknown = JSON.parse(event.data as string)
        if (typeof data !== "object" || data === null || !("type" in data)) {
          console.warn("[WS] Unknown message format:", data)
          return
        }

        const msg = data as ServerMessage

        // Handle auth handshake response
        if (this.pendingAuth && "id" in msg && (msg as { id: string }).id === "auth-handshake") {
          this.pendingAuth = false
          if (msg.type === "response") {
            console.log("[WS] Auth successful")
            this.options.onStatusChange("connected")
            this.options.onConnected?.()
          } else if (msg.type === "error") {
            console.error("[WS] Auth failed:", (msg as { error: { message: string } }).error.message)
            this.close()  // stop reconnect before callback
            this.options.onAuthFailed?.()
          }
          return
        }

        if (
          msg.type === "stream_chunk" ||
          msg.type === "error" ||
          msg.type === "tool_call" ||
          msg.type === "response" ||
          msg.type === "tool_denied"
        ) {
          this.options.onMessage(msg)
          return
        }
        console.warn("[WS] Unknown message type:", msg.type)
      } catch {
        console.warn("[WS] Failed to parse message:", event.data)
      }
    }

    this.ws.onclose = () => {
      if (this.intentionalClose) {
        this.options.onStatusChange("disconnected")
        return
      }
      console.log("[WS] Connection closed")
      this.attemptReconnect()
    }

    this.ws.onerror = (event: Event) => {
      console.error("[WS] Error:", event)
    }
  }

  send(request: RPCRequest): void {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      console.warn("[WS] Cannot send — not connected")
      return
    }
    this.ws.send(JSON.stringify(request))
  }

  close(): void {
    this.intentionalClose = true
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    this.options.onStatusChange("disconnected")
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  private attemptReconnect(): void {
    if (!this.options.reconnect) {
      this.options.onStatusChange("disconnected")
      return
    }

    if (this.reconnectAttempts >= this.options.maxReconnectAttempts) {
      console.log("[WS] Max reconnect attempts reached")
      this.options.onStatusChange("disconnected")
      return
    }

    this.reconnectAttempts++
    this.options.onStatusChange("reconnecting")

    // Exponential backoff with jitter: base * 2^(n-1) + random jitter
    const exponentialDelay = Math.min(
      this.options.baseReconnectMs * Math.pow(2, this.reconnectAttempts - 1),
      this.options.maxReconnectMs
    )
    const jitter = Math.random() * 500
    const delay = Math.round(exponentialDelay + jitter)

    console.log(
      `[WS] Reconnecting (attempt ${this.reconnectAttempts}) in ${delay}ms`
    )

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }
}
