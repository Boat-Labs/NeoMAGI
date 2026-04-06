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
const STORAGE_KEY = "neomagi-threads"
const HISTORY_TIMEOUT_MS = 10_000
const TITLE_MAX_LENGTH = 30

// --- Types ---

export interface ToolCall {
  callId: string
  toolName: string
  arguments: Record<string, unknown>
  status: "running" | "complete" | "denied" | "aborted"
  deniedInfo?: {
    mode: string
    errorCode: string
    message: string
    nextAction: string
  }
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

export interface SessionViewState {
  sessionId: string
  messages: ChatMessage[]
  pendingHistoryId: string | null
  isHistoryLoading: boolean
  isStreaming: boolean
  lastActivityAt: number
  title: string
  lastAssistantPreview: string
  hasUnreadCompletion: boolean
  lastError: string | null
  historyLoaded: boolean
}

export interface ChatState {
  activeSessionId: string
  sessionOrder: string[]
  sessionsById: Record<string, SessionViewState>
  requestToSession: Record<string, string>
  connectionStatus: ConnectionStatus

  connect: (url: string) => void
  disconnect: () => void
  sendMessage: (content: string) => boolean
  loadHistory: (sessionId?: string) => void
  createThread: () => void
  switchThread: (sessionId: string) => void
  _handleServerMessage: (message: ServerMessage) => void
  _setConnectionStatus: (status: ConnectionStatus) => void
}

// --- Helpers ---

export function createSessionViewState(
  sessionId: string,
  title?: string,
): SessionViewState {
  return {
    sessionId,
    messages: [],
    pendingHistoryId: null,
    isHistoryLoading: false,
    isStreaming: false,
    lastActivityAt: Date.now(),
    title: title ?? (sessionId === "main" ? "Main" : "New Thread"),
    lastAssistantPreview: "",
    hasUnreadCompletion: false,
    lastError: null,
    historyLoaded: false,
  }
}

function deriveTitle(messages: ChatMessage[]): string | null {
  const firstUser = messages.find((m) => m.role === "user")
  if (!firstUser?.content) return null
  const trimmed = firstUser.content.trim()
  if (!trimmed) return null
  return trimmed.length <= TITLE_MAX_LENGTH
    ? trimmed
    : trimmed.slice(0, TITLE_MAX_LENGTH) + "\u2026"
}

function isDefaultTitle(title: string): boolean {
  return title === "Main" || title === "New Thread"
}

function extractLastPreview(messages: ChatMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant" && messages[i].content) {
      return messages[i].content.slice(0, 80)
    }
  }
  return ""
}

// --- localStorage persistence ---

interface PersistedThreads {
  activeSessionId: string
  sessionOrder: string[]
  titles: Record<string, string>
  lastActivityAt?: Record<string, number>
}

function loadPersistedThreads(): PersistedThreads | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as PersistedThreads
  } catch {
    return null
  }
}

function persistThreads(state: {
  activeSessionId: string
  sessionOrder: string[]
  sessionsById: Record<string, SessionViewState>
}) {
  const titles: Record<string, string> = {}
  const lastActivityAt: Record<string, number> = {}
  for (const id of state.sessionOrder) {
    const s = state.sessionsById[id]
    if (s) {
      titles[id] = s.title
      lastActivityAt[id] = s.lastActivityAt
    }
  }
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        activeSessionId: state.activeSessionId,
        sessionOrder: state.sessionOrder,
        titles,
        lastActivityAt,
      }),
    )
  } catch {
    // localStorage full — ignore
  }
}

// --- Bootstrap ---

function bootstrapSessions(): {
  activeSessionId: string
  sessionOrder: string[]
  sessionsById: Record<string, SessionViewState>
} {
  const persisted = loadPersistedThreads()
  if (persisted && persisted.sessionOrder.length > 0) {
    const sessionsById: Record<string, SessionViewState> = {}
    for (const id of persisted.sessionOrder) {
      const session = createSessionViewState(id, persisted.titles[id])
      if (persisted.lastActivityAt?.[id]) {
        session.lastActivityAt = persisted.lastActivityAt[id]
      }
      sessionsById[id] = session
    }
    const activeSessionId = persisted.sessionOrder.includes(
      persisted.activeSessionId,
    )
      ? persisted.activeSessionId
      : persisted.sessionOrder[0]
    return { activeSessionId, sessionOrder: persisted.sessionOrder, sessionsById }
  }
  return {
    activeSessionId: "main",
    sessionOrder: ["main"],
    sessionsById: { main: createSessionViewState("main") },
  }
}

// --- Store ---

export const useChatStore = create<ChatState>()(
  devtools(
    (set, get) => {
      let wsClient: WebSocketClient | null = null
      const initial = bootstrapSessions()

      function updateSession(
        sessionId: string,
        updater: (s: SessionViewState) => Partial<SessionViewState>,
        actionName?: string,
      ) {
        set(
          (state) => {
            const session = state.sessionsById[sessionId]
            if (!session) return state
            return {
              sessionsById: {
                ...state.sessionsById,
                [sessionId]: { ...session, ...updater(session) },
              },
            }
          },
          false,
          actionName,
        )
      }

      function clearSessionHistoryGuard(sessionId: string) {
        updateSession(
          sessionId,
          () => ({ pendingHistoryId: null, isHistoryLoading: false }),
          "clearHistoryGuard",
        )
      }

      return {
        ...initial,
        requestToSession: {},
        connectionStatus: "disconnected" as ConnectionStatus,

        // ── Connection ──

        connect: (url: string) => {
          if (wsClient?.isConnected) return
          wsClient?.close()
          wsClient = new WebSocketClient({
            url,
            onMessage: (msg) =>
              useChatStore.getState()._handleServerMessage(msg),
            onStatusChange: (status) =>
              useChatStore.getState()._setConnectionStatus(status),
            onConnected: () => {
              const { activeSessionId } = useChatStore.getState()
              useChatStore.getState().loadHistory(activeSessionId)
            },
          })
          wsClient.connect()
        },

        disconnect: () => {
          wsClient?.close()
          wsClient = null
        },

        // ── History ──

        loadHistory: (sessionId?: string) => {
          const sid = sessionId ?? get().activeSessionId
          if (!wsClient?.isConnected) return
          const session = get().sessionsById[sid]
          if (!session || session.pendingHistoryId) return

          const requestId = crypto.randomUUID()
          updateSession(
            sid,
            () => ({ pendingHistoryId: requestId, isHistoryLoading: true }),
            "historyLoading",
          )

          wsClient.send({
            type: "request",
            id: requestId,
            method: "chat.history",
            params: { session_id: sid },
          })

          setTimeout(() => {
            const s = get().sessionsById[sid]
            if (s?.pendingHistoryId === requestId) {
              clearSessionHistoryGuard(sid)
              toast.warning(
                "History loading timed out. You can continue chatting.",
              )
            }
          }, HISTORY_TIMEOUT_MS)
        },

        // ── Send ──

        sendMessage: (content: string): boolean => {
          if (!wsClient?.isConnected) return false
          const state = get()
          const session = state.sessionsById[state.activeSessionId]
          if (!session || session.pendingHistoryId !== null) return false

          const requestId = crypto.randomUUID()
          const sessionId = state.activeSessionId

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
            (prev) => {
              const s = prev.sessionsById[sessionId]
              if (!s) return prev
              const newMessages = [...s.messages, userMessage, assistantMessage]
              return {
                sessionsById: {
                  ...prev.sessionsById,
                  [sessionId]: {
                    ...s,
                    messages: newMessages,
                    isStreaming: true,
                    lastActivityAt: Date.now(),
                    title: isDefaultTitle(s.title)
                      ? (deriveTitle(newMessages) ?? s.title)
                      : s.title,
                  },
                },
                requestToSession: {
                  ...prev.requestToSession,
                  [requestId]: sessionId,
                },
              }
            },
            false,
            "sendMessage",
          )

          persistThreads(get())

          wsClient.send({
            type: "request",
            id: requestId,
            method: "chat.send",
            params: { content, session_id: sessionId } satisfies ChatSendParams,
          })
          return true
        },

        // ── Thread management ──

        createThread: () => {
          const state = get()
          const sessionId = `web:${crypto.randomUUID()}`
          set(
            {
              activeSessionId: sessionId,
              sessionOrder: [sessionId, ...state.sessionOrder],
              sessionsById: {
                ...state.sessionsById,
                [sessionId]: createSessionViewState(sessionId),
              },
            },
            false,
            "createThread",
          )
          persistThreads(get())
        },

        switchThread: (sessionId: string) => {
          const state = get()
          const session = state.sessionsById[sessionId]
          if (!session) return

          set({ activeSessionId: sessionId }, false, "switchThread")

          if (session.hasUnreadCompletion) {
            updateSession(
              sessionId,
              () => ({ hasUnreadCompletion: false }),
              "clearUnread",
            )
          }

          // Lazy-load history if never loaded
          if (
            !session.historyLoaded &&
            session.messages.length === 0 &&
            !session.pendingHistoryId
          ) {
            get().loadHistory(sessionId)
          }

          persistThreads(get())
        },

        // ── Server message routing ──

        _handleServerMessage: (message: ServerMessage) => {
          const state = get()

          switch (message.type) {
            case "stream_chunk": {
              const sessionId = state.requestToSession[message.id]
              if (!sessionId) break

              if (message.data.done) {
                const isActive = sessionId === state.activeSessionId
                set(
                  (prev) => {
                    const s = prev.sessionsById[sessionId]
                    if (!s) return prev
                    const updatedMessages = s.messages.map((m) =>
                      m.id === message.id
                        ? {
                            ...m,
                            status: "complete" as const,
                            toolCalls: m.toolCalls?.map((tc) =>
                              tc.status === "denied" ||
                              tc.status === "aborted"
                                ? tc
                                : { ...tc, status: "complete" as const },
                            ),
                          }
                        : m,
                    )
                    const cleanedRequests = { ...prev.requestToSession }
                    delete cleanedRequests[message.id]
                    return {
                      requestToSession: cleanedRequests,
                      sessionsById: {
                        ...prev.sessionsById,
                        [sessionId]: {
                          ...s,
                          isStreaming: false,
                          lastActivityAt: Date.now(),
                          lastAssistantPreview:
                            extractLastPreview(updatedMessages),
                          hasUnreadCompletion: isActive
                            ? s.hasUnreadCompletion
                            : true,
                          messages: updatedMessages,
                        },
                      },
                    }
                  },
                  false,
                  "streamComplete",
                )
                persistThreads(get())
              } else {
                updateSession(
                  sessionId,
                  (s) => ({
                    messages: s.messages.map((m) =>
                      m.id === message.id
                        ? {
                            ...m,
                            content: m.content + message.data.content,
                            toolCalls: m.toolCalls?.map((tc) =>
                              tc.status === "running"
                                ? { ...tc, status: "complete" as const }
                                : tc,
                            ),
                          }
                        : m,
                    ),
                  }),
                  "streamChunk",
                )
              }
              break
            }

            case "error": {
              // Check if it's a history response error
              for (const [sid, session] of Object.entries(
                state.sessionsById,
              )) {
                if (session.pendingHistoryId === message.id) {
                  clearSessionHistoryGuard(sid)
                  break
                }
              }

              // Check if it's a request error
              const sessionId = state.requestToSession[message.id]
              if (sessionId) {
                set(
                  (prev) => {
                    const s = prev.sessionsById[sessionId]
                    if (!s) return prev
                    const cleanedRequests = { ...prev.requestToSession }
                    delete cleanedRequests[message.id]
                    return {
                      requestToSession: cleanedRequests,
                      sessionsById: {
                        ...prev.sessionsById,
                        [sessionId]: {
                          ...s,
                          isStreaming: false,
                          lastError: message.error.message,
                          messages: s.messages.map((m) =>
                            m.id === message.id
                              ? {
                                  ...m,
                                  status: "error" as const,
                                  error: message.error.message,
                                }
                              : m,
                          ),
                        },
                      },
                    }
                  },
                  false,
                  "streamError",
                )
              }

              const isFatal = FATAL_ERROR_CODES.has(message.error.code)
              toast.error(message.error.message, {
                duration: isFatal ? Infinity : 5000,
              })
              break
            }

            case "tool_call": {
              const sessionId = state.requestToSession[message.id]
              if (!sessionId) break

              const newToolCall: ToolCall = {
                callId: message.data.call_id,
                toolName: message.data.tool_name,
                arguments: message.data.arguments,
                status: "running",
              }

              updateSession(
                sessionId,
                (s) => ({
                  messages: s.messages.map((m) =>
                    m.id === message.id
                      ? {
                          ...m,
                          toolCalls: [...(m.toolCalls ?? []), newToolCall],
                        }
                      : m,
                  ),
                }),
                "toolCall",
              )
              break
            }

            case "tool_denied": {
              const sessionId = state.requestToSession[message.id]
              if (!sessionId) break

              const deniedInfo = {
                mode: message.data.mode,
                errorCode: message.data.error_code,
                message: message.data.message,
                nextAction: message.data.next_action,
              }

              updateSession(
                sessionId,
                (s) => ({
                  messages: s.messages.map((m) => {
                    if (m.id !== message.id) return m
                    const existing = m.toolCalls ?? []
                    const idx = existing.findIndex(
                      (tc) => tc.callId === message.data.call_id,
                    )
                    if (idx >= 0) {
                      const updated = [...existing]
                      updated[idx] = {
                        ...updated[idx],
                        status: "denied" as const,
                        deniedInfo,
                      }
                      return { ...m, toolCalls: updated }
                    }
                    return {
                      ...m,
                      toolCalls: [
                        ...existing,
                        {
                          callId: message.data.call_id,
                          toolName: message.data.tool_name,
                          arguments: {},
                          status: "denied" as const,
                          deniedInfo,
                        },
                      ],
                    }
                  }),
                }),
                "toolDenied",
              )
              break
            }

            case "response": {
              // History response — route by pendingHistoryId
              let targetSessionId: string | null = null
              for (const [sid, session] of Object.entries(
                state.sessionsById,
              )) {
                if (session.pendingHistoryId === message.id) {
                  targetSessionId = sid
                  break
                }
              }
              if (!targetSessionId) break

              clearSessionHistoryGuard(targetSessionId)

              const historyMessages: ChatMessage[] =
                message.data.messages.map((hm: HistoryMessage) => ({
                  id: crypto.randomUUID(),
                  role: hm.role,
                  content: hm.content,
                  timestamp: hm.timestamp
                    ? new Date(hm.timestamp).getTime()
                    : Date.now(),
                  status: "complete" as const,
                }))

              updateSession(
                targetSessionId,
                (s) => ({
                  messages: historyMessages,
                  isStreaming: false,
                  historyLoaded: true,
                  title: isDefaultTitle(s.title)
                    ? (deriveTitle(historyMessages) ?? s.title)
                    : s.title,
                  lastAssistantPreview: extractLastPreview(historyMessages),
                  lastActivityAt:
                    historyMessages.length > 0
                      ? historyMessages[historyMessages.length - 1].timestamp
                      : s.lastActivityAt,
                }),
                "loadHistory",
              )

              persistThreads(get())
              break
            }
          }
        },

        // ── Connection status ──

        _setConnectionStatus: (status: ConnectionStatus) => {
          if (status === "reconnecting" || status === "disconnected") {
            // Clear ALL sessions' pending history guards + in-flight requests
            set(
              (prev) => {
                const updatedSessions = { ...prev.sessionsById }

                // Clear pending history guards
                for (const [sid, session] of Object.entries(
                  updatedSessions,
                )) {
                  if (session.pendingHistoryId !== null) {
                    updatedSessions[sid] = {
                      ...session,
                      pendingHistoryId: null,
                      isHistoryLoading: false,
                    }
                  }
                }

                // Collect in-flight request IDs per session
                const inflightBySession = new Map<string, Set<string>>()
                for (const [reqId, sessionId] of Object.entries(
                  prev.requestToSession,
                )) {
                  if (!inflightBySession.has(sessionId)) {
                    inflightBySession.set(sessionId, new Set())
                  }
                  inflightBySession.get(sessionId)!.add(reqId)
                }

                // Reset isStreaming + mark messages/tools as terminal
                for (const [sessionId, reqIds] of inflightBySession) {
                  const session = updatedSessions[sessionId]
                  if (!session) continue
                  updatedSessions[sessionId] = {
                    ...session,
                    isStreaming: false,
                    messages: session.messages.map((m) =>
                      reqIds.has(m.id) && m.status === "streaming"
                        ? {
                            ...m,
                            status: "error" as const,
                            error: "Connection lost",
                            toolCalls: m.toolCalls?.map((tc) =>
                              tc.status === "running"
                                ? { ...tc, status: "aborted" as const }
                                : tc,
                            ),
                          }
                        : m,
                    ),
                  }
                }

                return {
                  sessionsById: updatedSessions,
                  requestToSession: {},
                }
              },
              false,
              "connectionLost",
            )
          }

          const prev = get().connectionStatus
          set({ connectionStatus: status }, false, "connectionStatus")

          if (prev === "connected" && status === "reconnecting") {
            toast.warning("Connection lost, reconnecting...")
          } else if (
            status === "disconnected" &&
            prev === "reconnecting"
          ) {
            toast.error("Failed to reconnect. Please refresh the page.", {
              duration: Infinity,
            })
          }
        },
      }
    },
    { name: "ChatStore" },
  ),
)
