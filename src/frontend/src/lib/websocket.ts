import type { RPCRequest, ServerMessage, ConnectionStatus } from "@/types/rpc"

export interface WebSocketClientOptions {
  url: string
  onMessage: (message: ServerMessage) => void
  onStatusChange: (status: ConnectionStatus) => void
  onConnected?: () => void
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
  private options: Required<Omit<WebSocketClientOptions, "onConnected">> & {
    onConnected?: () => void
  }
  private reconnectAttempts = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private intentionalClose = false

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
      this.options.onStatusChange("connected")
      this.options.onConnected?.()
    }

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const data: unknown = JSON.parse(event.data as string)
        if (typeof data === "object" && data !== null && "type" in data) {
          const msg = data as ServerMessage
          if (
            msg.type === "stream_chunk" ||
            msg.type === "error" ||
            msg.type === "tool_call" ||
            msg.type === "response"
          ) {
            this.options.onMessage(msg)
            return
          }
        }
        console.warn("[WS] Unknown message format:", data)
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
