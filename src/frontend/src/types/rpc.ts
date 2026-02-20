// WebSocket RPC protocol types — single source of truth for frontend ↔ backend contract

export type RequestId = string

// === Client → Server ===

export interface RPCRequest {
  type: "request"
  id: RequestId
  method: string
  params: Record<string, unknown>
}

export interface ChatSendParams {
  content: string
  session_id: string
}

// === Server → Client ===

export interface StreamChunkMessage {
  type: "stream_chunk"
  id: RequestId
  data: {
    content: string
    done: boolean
  }
}

export interface ErrorMessage {
  type: "error"
  id: RequestId
  error: {
    code: string
    message: string
  }
}

export interface ToolCallMessage {
  type: "tool_call"
  id: RequestId
  data: {
    tool_name: string
    arguments: Record<string, unknown>
    call_id: string
  }
}

export interface HistoryMessage {
  role: "user" | "assistant"
  content: string
  timestamp?: string
}

export interface ResponseMessage {
  type: "response"
  id: RequestId
  data: {
    messages: HistoryMessage[]
  }
}

export interface ToolDeniedMessage {
  type: "tool_denied"
  id: RequestId
  data: {
    call_id: string
    tool_name: string
    mode: string
    error_code: string
    message: string
    next_action: string
  }
}

export type ServerMessage = StreamChunkMessage | ErrorMessage | ToolCallMessage | ResponseMessage | ToolDeniedMessage

export type ConnectionStatus = "disconnected" | "connecting" | "connected" | "reconnecting"
