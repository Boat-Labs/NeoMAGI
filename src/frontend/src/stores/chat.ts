import { create } from "zustand"
import { devtools } from "zustand/middleware"
import { toast } from "sonner"
import { WebSocketClient } from "@/lib/websocket"
import type {
  ConnectionStatus,
  ServerMessage,
  ChatSendParams,
} from "@/types/rpc"

// Error codes considered non-recoverable (persistent toast)
const FATAL_ERROR_CODES = new Set(["INTERNAL_ERROR", "LLM_ERROR"])

export interface ToolCall {
  callId: string
  toolName: string
  arguments: Record<string, unknown>
  status: "running" | "complete"
}

export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
  timestamp: number
  status: "sending" | "streaming" | "complete" | "error"
  error?: string
  toolCalls?: ToolCall[]
}

interface ChatState {
  messages: ChatMessage[]
  connectionStatus: ConnectionStatus
  isStreaming: boolean

  connect: (url: string) => void
  disconnect: () => void
  sendMessage: (content: string) => void

  // Internal — called by WebSocket callbacks
  _handleServerMessage: (message: ServerMessage) => void
  _setConnectionStatus: (status: ConnectionStatus) => void
}

export const useChatStore = create<ChatState>()(
  devtools(
    (set, _get) => {
      let wsClient: WebSocketClient | null = null

      return {
        messages: [],
        connectionStatus: "disconnected" as ConnectionStatus,
        isStreaming: false,

        connect: (url: string) => {
          if (wsClient?.isConnected) return

          wsClient?.close()
          wsClient = new WebSocketClient({
            url,
            onMessage: (msg) => {
              const store = useChatStore.getState()
              store._handleServerMessage(msg)
            },
            onStatusChange: (status) => {
              const store = useChatStore.getState()
              store._setConnectionStatus(status)
            },
          })
          wsClient.connect()
        },

        disconnect: () => {
          wsClient?.close()
          wsClient = null
        },

        sendMessage: (content: string) => {
          if (!wsClient?.isConnected) return

          const requestId = crypto.randomUUID()

          const userMessage: ChatMessage = {
            id: crypto.randomUUID(),
            role: "user",
            content,
            timestamp: Date.now(),
            status: "complete",
          }

          const assistantMessage: ChatMessage = {
            id: requestId,
            role: "assistant",
            content: "",
            timestamp: Date.now(),
            status: "streaming",
          }

          set(
            (state) => ({
              messages: [...state.messages, userMessage, assistantMessage],
              isStreaming: true,
            }),
            false,
            "sendMessage"
          )

          wsClient.send({
            type: "request",
            id: requestId,
            method: "chat.send",
            params: {
              content,
              session_id: "main",
            } satisfies ChatSendParams,
          })
        },

        _handleServerMessage: (message: ServerMessage) => {
          switch (message.type) {
            case "stream_chunk": {
              if (message.data.done) {
                set(
                  (state) => ({
                    isStreaming: false,
                    messages: state.messages.map((m) =>
                      m.id === message.id
                        ? {
                            ...m,
                            status: "complete" as const,
                            toolCalls: m.toolCalls?.map((tc) => ({
                              ...tc,
                              status: "complete" as const,
                            })),
                          }
                        : m
                    ),
                  }),
                  false,
                  "streamComplete"
                )
              } else {
                set(
                  (state) => ({
                    messages: state.messages.map((m) =>
                      m.id === message.id
                        ? {
                            ...m,
                            content: m.content + message.data.content,
                            // Mark running tool calls as complete when new text arrives
                            toolCalls: m.toolCalls?.map((tc) =>
                              tc.status === "running"
                                ? { ...tc, status: "complete" as const }
                                : tc
                            ),
                          }
                        : m
                    ),
                  }),
                  false,
                  "streamChunk"
                )
              }
              break
            }
            case "error": {
              set(
                (state) => ({
                  isStreaming: false,
                  messages: state.messages.map((m) =>
                    m.id === message.id
                      ? {
                          ...m,
                          status: "error" as const,
                          error: message.error.message,
                        }
                      : m
                  ),
                }),
                false,
                "streamError"
              )
              // Toast notification
              const isFatal = FATAL_ERROR_CODES.has(message.error.code)
              toast.error(message.error.message, {
                duration: isFatal ? Infinity : 5000,
              })
              break
            }
            case "tool_call": {
              const newToolCall: ToolCall = {
                callId: message.data.call_id,
                toolName: message.data.tool_name,
                arguments: message.data.arguments,
                status: "running",
              }
              set(
                (state) => ({
                  messages: state.messages.map((m) =>
                    m.id === message.id
                      ? {
                          ...m,
                          toolCalls: [...(m.toolCalls ?? []), newToolCall],
                        }
                      : m
                  ),
                }),
                false,
                "toolCall"
              )
              break
            }
          }
        },

        _setConnectionStatus: (status: ConnectionStatus) => {
          const prev = useChatStore.getState().connectionStatus
          set({ connectionStatus: status }, false, "connectionStatus")

          // Toast on connection state transitions
          if (prev === "connected" && status === "reconnecting") {
            toast.warning("Connection lost, reconnecting...")
          } else if (status === "disconnected" && prev === "reconnecting") {
            toast.error("Failed to reconnect. Please refresh the page.", {
              duration: Infinity,
            })
          }
        },
      }
    },
    { name: "ChatStore" }
  )
)
