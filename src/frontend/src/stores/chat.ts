import { create } from "zustand"
import { devtools } from "zustand/middleware"
import { toast } from "sonner"
import { WebSocketClient } from "@/lib/websocket"
import type {
  ConnectionStatus,
  ServerMessage,
  ChatSendParams,
  HistoryMessage,
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
  isHistoryLoading: boolean

  connect: (url: string) => void
  disconnect: () => void
  sendMessage: (content: string) => boolean
  loadHistory: () => void

  // Internal — called by WebSocket callbacks
  _handleServerMessage: (message: ServerMessage) => void
  _setConnectionStatus: (status: ConnectionStatus) => void
}

export const useChatStore = create<ChatState>()(
  devtools(
    (set, _get) => {
      let wsClient: WebSocketClient | null = null
      let pendingHistoryId: string | null = null

      function clearHistoryGuard() {
        pendingHistoryId = null
        set({ isHistoryLoading: false }, false, "historyLoaded")
      }

      return {
        messages: [],
        connectionStatus: "disconnected" as ConnectionStatus,
        isStreaming: false,
        isHistoryLoading: false,

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
            onConnected: () => {
              const store = useChatStore.getState()
              store.loadHistory()
            },
          })
          wsClient.connect()
        },

        disconnect: () => {
          wsClient?.close()
          wsClient = null
        },

        loadHistory: () => {
          if (!wsClient?.isConnected) return
          const requestId = crypto.randomUUID()
          pendingHistoryId = requestId
          set({ isHistoryLoading: true }, false, "historyLoading")
          wsClient.send({
            type: "request",
            id: requestId,
            method: "chat.history",
            params: { session_id: "main" },
          })

          // Timeout guard: prevent indefinite pending state
          setTimeout(() => {
            if (pendingHistoryId === requestId) {
              clearHistoryGuard()
              toast.warning("History loading timed out. You can continue chatting.")
            }
          }, 10_000)
        },

        sendMessage: (content: string): boolean => {
          if (!wsClient?.isConnected) return false
          if (pendingHistoryId !== null) return false

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
          return true
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
              if (message.id === pendingHistoryId) {
                clearHistoryGuard()
              }
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
            case "response": {
              // Handle history response
              if (message.id !== pendingHistoryId) break
              clearHistoryGuard()

              const historyMessages: ChatMessage[] = message.data.messages.map(
                (hm: HistoryMessage) => ({
                  id: crypto.randomUUID(),
                  role: hm.role,
                  content: hm.content,
                  timestamp: hm.timestamp
                    ? new Date(hm.timestamp).getTime()
                    : Date.now(),
                  status: "complete" as const,
                })
              )

              // [Decision 0021] Full replacement — no dedup merge
              set(
                { messages: historyMessages, isStreaming: false },
                false,
                "loadHistory"
              )
              break
            }
          }
        },

        _setConnectionStatus: (status: ConnectionStatus) => {
          if (status === "reconnecting" || status === "disconnected") {
            if (pendingHistoryId !== null) {
              clearHistoryGuard()
            }
          }
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
