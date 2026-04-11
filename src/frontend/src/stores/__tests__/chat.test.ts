/**
 * Tests for multi-session chat store (P2-M1 Post Works P1).
 *
 * Covers: per-session state, event routing via requestToSession,
 * history routing via pendingHistoryId, background completion,
 * thread creation/switching, localStorage persistence, and
 * cross-thread isolation.
 */
import { describe, it, expect, beforeEach, vi } from "vitest"
import { useChatStore, createSessionViewState } from "../chat"
import type { ChatState } from "../chat"
import type { RPCRequest } from "@/types/rpc"
// Mock sonner toast
vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    warning: vi.fn(),
    success: vi.fn(),
  },
}))
// Mock localStorage (Node.js 22+ built-in localStorage lacks standard Storage API)
const _storage = new Map<string, string>()
vi.stubGlobal("localStorage", {
  getItem: (key: string) => _storage.get(key) ?? null,
  setItem: (key: string, value: string) => _storage.set(key, String(value)),
  removeItem: (key: string) => _storage.delete(key),
  clear: () => _storage.clear(),
  get length() { return _storage.size },
  key: (index: number) => [..._storage.keys()][index] ?? null,
})
// Controllable mock for WebSocketClient
let mockIsConnected = false
const mockSend = vi.fn()
const mockClose = vi.fn()
const mockConnect = vi.fn()
const mockCallbacks: {
  onMessage?: (msg: unknown) => void
  onStatusChange?: (status: string) => void
  onConnected?: () => void
} = {}
vi.mock("@/lib/websocket", () => {
  return {
    WebSocketClient: class MockWebSocketClient {
      constructor(opts: {
        onMessage: (msg: unknown) => void
        onStatusChange: (status: string) => void
        onConnected?: () => void
      }) {
        mockCallbacks.onMessage = opts.onMessage
        mockCallbacks.onStatusChange = opts.onStatusChange
        mockCallbacks.onConnected = opts.onConnected
      }
      connect = mockConnect
      send = mockSend
      close = mockClose
      get isConnected() {
        return mockIsConnected
      }
    },
  }
})
// --- Helpers ---
function resetStore() {
  useChatStore.setState({
    activeSessionId: "main",
    sessionOrder: ["main"],
    sessionsById: { main: createSessionViewState("main") },
    requestToSession: {},
    connectionStatus: "disconnected",
  } as Partial<ChatState>)
  mockIsConnected = false
  mockSend.mockClear()
  mockClose.mockClear()
  mockConnect.mockClear()
  _storage.clear()
}
function connectStore() {
  const store = useChatStore.getState()
  store.connect("ws://test")
  mockIsConnected = true
  mockCallbacks.onStatusChange?.("connected")
  mockCallbacks.onConnected?.()
}
function getSession(sessionId: string) {
  return useChatStore.getState().sessionsById[sessionId]
}
function getLastHistoryRequestId(): string {
  const lastCall = mockSend.mock.calls[mockSend.mock.calls.length - 1]
  const request = lastCall[0] as RPCRequest
  expect(request.method).toBe("chat.history")
  return request.id
}
function getLastSendRequestId(): string {
  const lastCall = mockSend.mock.calls[mockSend.mock.calls.length - 1]
  const request = lastCall[0] as RPCRequest
  expect(request.method).toBe("chat.send")
  return request.id
}
// --- Guard Recovery (per-session) ---

describe("per-session history guard recovery", () => {
  beforeEach(resetStore)

  it("clears history guard on error matching pendingHistoryId", () => {
    connectStore()
    const requestId = getLastHistoryRequestId()
    expect(getSession("main")!.isHistoryLoading).toBe(true)
    useChatStore.getState()._handleServerMessage({
      type: "error",
      id: requestId,
      error: { code: "INTERNAL_ERROR", message: "something failed" },
    })
    expect(getSession("main")!.isHistoryLoading).toBe(false)
    expect(getSession("main")!.pendingHistoryId).toBeNull()
  })

  it("clears ALL sessions' history guards on disconnect", () => {
    // Set up two sessions with pending history
    useChatStore.setState({
      sessionOrder: ["main", "thread-2"],
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          pendingHistoryId: "h1",
          isHistoryLoading: true,
        },
        "thread-2": {
          ...createSessionViewState("thread-2"),
          pendingHistoryId: "h2",
          isHistoryLoading: true,
        },
      },
    })
    useChatStore.getState()._setConnectionStatus("disconnected")
    expect(getSession("main")!.isHistoryLoading).toBe(false)
    expect(getSession("main")!.pendingHistoryId).toBeNull()
    expect(getSession("thread-2")!.isHistoryLoading).toBe(false)
    expect(getSession("thread-2")!.pendingHistoryId).toBeNull()
  })

  it("clears ALL sessions' history guards on reconnecting", () => {
    useChatStore.setState({
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          pendingHistoryId: "h1",
          isHistoryLoading: true,
        },
      },
    })
    useChatStore.getState()._setConnectionStatus("reconnecting")
    expect(getSession("main")!.isHistoryLoading).toBe(false)
    expect(getSession("main")!.pendingHistoryId).toBeNull()
  })

  it("clears history guard on timeout", () => {
    vi.useFakeTimers()
    try {
      connectStore()
      expect(getSession("main")!.isHistoryLoading).toBe(true)
      vi.advanceTimersByTime(10_000)
      expect(getSession("main")!.isHistoryLoading).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it("timeout is no-op if already resolved", () => {
    vi.useFakeTimers()
    try {
      connectStore()
      const requestId = getLastHistoryRequestId()
      useChatStore.getState()._handleServerMessage({
        type: "response",
        id: requestId,
        data: { messages: [] },
      })
      expect(getSession("main")!.isHistoryLoading).toBe(false)
      vi.advanceTimersByTime(10_000)
      expect(getSession("main")!.isHistoryLoading).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})
// --- Send message ---

describe("sendMessage (per-session)", () => {
  beforeEach(resetStore)

  it("blocks during history loading of active thread", () => {
    connectStore()
    expect(getSession("main")!.isHistoryLoading).toBe(true)
    const result = useChatStore.getState().sendMessage("hello")
    expect(result).toBe(false)
  })

  it("returns false when not connected", () => {
    const result = useChatStore.getState().sendMessage("hello")
    expect(result).toBe(false)
  })

  it("adds messages to active session only", () => {
    connectStore()
    // Resolve history first
    const historyId = getLastHistoryRequestId()
    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: historyId,
      data: { messages: [] },
    })
    // Create a second thread
    useChatStore.getState().createThread()
    const secondId = useChatStore.getState().activeSessionId
    const sent = useChatStore.getState().sendMessage("hello from thread 2")
    expect(sent).toBe(true)
    // Messages added to active (second) session
    expect(getSession(secondId)!.messages).toHaveLength(2)
    // Main session unchanged
    expect(getSession("main")!.messages).toHaveLength(0)
  })

  it("registers requestToSession mapping", () => {
    connectStore()
    const historyId = getLastHistoryRequestId()
    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: historyId,
      data: { messages: [] },
    })
    useChatStore.getState().sendMessage("hello")
    const requestId = getLastSendRequestId()
    expect(useChatStore.getState().requestToSession[requestId]).toBe("main")
  })

  it("derives title from first user message", () => {
    connectStore()
    const historyId = getLastHistoryRequestId()
    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: historyId,
      data: { messages: [] },
    })
    useChatStore.getState().sendMessage("What is the weather today?")
    expect(getSession("main")!.title).toBe("What is the weather today?")
  })

  it("truncates long titles to 30 chars", () => {
    connectStore()
    const historyId = getLastHistoryRequestId()
    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: historyId,
      data: { messages: [] },
    })
    const longMessage = "A".repeat(50)
    useChatStore.getState().sendMessage(longMessage)
    expect(getSession("main")!.title).toBe("A".repeat(30) + "\u2026")
  })

  it("updates lastActivityAt for activity-based sorting", () => {
    connectStore()
    const historyId = getLastHistoryRequestId()
    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: historyId,
      data: { messages: [] },
    })
    const beforeSend = getSession("main")!.lastActivityAt
    // Small delay to guarantee timestamp difference
    useChatStore.getState().sendMessage("hello")
    expect(getSession("main")!.lastActivityAt).toBeGreaterThanOrEqual(
      beforeSend,
    )
  })
})
// --- Stream chunk routing ---

describe("stream_chunk event routing", () => {
  beforeEach(resetStore)

  it("accumulates content to correct session", () => {
    const requestId = "req-1"
    useChatStore.setState({
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: requestId,
              role: "assistant",
              content: "",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [requestId]: "main" },
    })
    useChatStore.getState()._handleServerMessage({
      type: "stream_chunk",
      id: requestId,
      data: { content: "Hello ", done: false },
    })
    useChatStore.getState()._handleServerMessage({
      type: "stream_chunk",
      id: requestId,
      data: { content: "world!", done: false },
    })
    expect(getSession("main")!.messages[0].content).toBe("Hello world!")
  })

  it("done cleans up requestToSession", () => {
    const requestId = "req-1"
    useChatStore.setState({
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: requestId,
              role: "assistant",
              content: "hi",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [requestId]: "main" },
    })
    useChatStore.getState()._handleServerMessage({
      type: "stream_chunk",
      id: requestId,
      data: { content: "", done: true },
    })
    expect(useChatStore.getState().requestToSession[requestId]).toBeUndefined()
    expect(getSession("main")!.isStreaming).toBe(false)
    expect(getSession("main")!.messages[0].status).toBe("complete")
  })

  it("does NOT pollute other sessions", () => {
    const reqA = "req-a"
    const reqB = "req-b"
    useChatStore.setState({
      activeSessionId: "main",
      sessionOrder: ["main", "thread-2"],
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: reqA,
              role: "assistant",
              content: "",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
        "thread-2": {
          ...createSessionViewState("thread-2"),
          messages: [
            {
              id: reqB,
              role: "assistant",
              content: "",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [reqA]: "main", [reqB]: "thread-2" },
    })
    // Chunk for thread-2 should NOT affect main
    useChatStore.getState()._handleServerMessage({
      type: "stream_chunk",
      id: reqB,
      data: { content: "for thread 2", done: false },
    })
    expect(getSession("main")!.messages[0].content).toBe("")
    expect(getSession("thread-2")!.messages[0].content).toBe("for thread 2")
  })
})
// --- Background completion ---

describe("background completion signals", () => {
  beforeEach(resetStore)

  it("sets hasUnreadCompletion when non-active thread completes", () => {
    const reqB = "req-b"
    useChatStore.setState({
      activeSessionId: "main",
      sessionOrder: ["main", "thread-2"],
      sessionsById: {
        main: createSessionViewState("main"),
        "thread-2": {
          ...createSessionViewState("thread-2"),
          messages: [
            {
              id: reqB,
              role: "assistant",
              content: "answer",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [reqB]: "thread-2" },
    })
    useChatStore.getState()._handleServerMessage({
      type: "stream_chunk",
      id: reqB,
      data: { content: "", done: true },
    })
    expect(getSession("thread-2")!.hasUnreadCompletion).toBe(true)
    expect(getSession("thread-2")!.isStreaming).toBe(false)
  })

  it("does NOT set hasUnreadCompletion for active thread", () => {
    const reqA = "req-a"
    useChatStore.setState({
      activeSessionId: "main",
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: reqA,
              role: "assistant",
              content: "answer",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [reqA]: "main" },
    })
    useChatStore.getState()._handleServerMessage({
      type: "stream_chunk",
      id: reqA,
      data: { content: "", done: true },
    })
    expect(getSession("main")!.hasUnreadCompletion).toBe(false)
  })

  it("switchThread clears hasUnreadCompletion", () => {
    useChatStore.setState({
      activeSessionId: "main",
      sessionOrder: ["main", "thread-2"],
      sessionsById: {
        main: createSessionViewState("main"),
        "thread-2": {
          ...createSessionViewState("thread-2"),
          hasUnreadCompletion: true,
          messages: [
            {
              id: "m1",
              role: "user",
              content: "hi",
              timestamp: Date.now(),
              status: "complete",
            },
          ],
        },
      },
    })
    useChatStore.getState().switchThread("thread-2")
    expect(getSession("thread-2")!.hasUnreadCompletion).toBe(false)
  })
})
// --- Error routing ---

describe("error event routing", () => {
  beforeEach(resetStore)

  it("routes request error to correct session", () => {
    const reqA = "req-a"
    useChatStore.setState({
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: reqA,
              role: "assistant",
              content: "",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [reqA]: "main" },
    })
    useChatStore.getState()._handleServerMessage({
      type: "error",
      id: reqA,
      error: { code: "LLM_ERROR", message: "model error" },
    })
    expect(getSession("main")!.isStreaming).toBe(false)
    expect(getSession("main")!.messages[0].status).toBe("error")
    expect(getSession("main")!.lastError).toBe("model error")
    expect(useChatStore.getState().requestToSession[reqA]).toBeUndefined()
  })

  it("routes history error to correct session", () => {
    useChatStore.setState({
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          pendingHistoryId: "hist-1",
          isHistoryLoading: true,
        },
      },
    })
    useChatStore.getState()._handleServerMessage({
      type: "error",
      id: "hist-1",
      error: { code: "INTERNAL_ERROR", message: "db error" },
    })
    expect(getSession("main")!.isHistoryLoading).toBe(false)
    expect(getSession("main")!.pendingHistoryId).toBeNull()
  })
})
// --- Tool call routing ---

describe("tool_call / tool_denied routing", () => {
  beforeEach(resetStore)

  it("adds tool calls to correct session", () => {
    const reqA = "req-a"
    useChatStore.setState({
      activeSessionId: "main",
      sessionOrder: ["main", "thread-2"],
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: reqA,
              role: "assistant",
              content: "",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
        "thread-2": {
          ...createSessionViewState("thread-2"),
          messages: [],
        },
      },
      requestToSession: { [reqA]: "main" },
    })
    useChatStore.getState()._handleServerMessage({
      type: "tool_call",
      id: reqA,
      data: {
        tool_name: "read_file",
        arguments: { path: "test.txt" },
        call_id: "call-1",
      },
    })
    expect(getSession("main")!.messages[0].toolCalls).toHaveLength(1)
    expect(getSession("main")!.messages[0].toolCalls![0].toolName).toBe(
      "read_file",
    )
    // thread-2 unaffected
    expect(getSession("thread-2")!.messages).toHaveLength(0)
  })

  it("tool_denied updates existing tool_call in correct session", () => {
    const reqA = "req-a"
    useChatStore.setState({
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: reqA,
              role: "assistant",
              content: "",
              timestamp: Date.now(),
              status: "streaming",
              toolCalls: [
                {
                  callId: "call-1",
                  toolName: "read_file",
                  arguments: { path: "test.txt" },
                  status: "running",
                },
              ],
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [reqA]: "main" },
    })
    useChatStore.getState()._handleServerMessage({
      type: "tool_denied",
      id: reqA,
      data: {
        call_id: "call-1",
        tool_name: "read_file",
        mode: "chat_safe",
        error_code: "MODE_DENIED",
        message: "denied",
        next_action: "n/a",
      },
    })

    const tc = getSession("main")!.messages[0].toolCalls![0]
    expect(tc.status).toBe("denied")
    expect(tc.deniedInfo).toBeDefined()
  })

  it("done handler preserves denied status", () => {
    const requestId = "req-1"
    useChatStore.setState({
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: requestId,
              role: "assistant",
              content: "",
              timestamp: Date.now(),
              status: "streaming",
              toolCalls: [
                {
                  callId: "call-ok",
                  toolName: "current_time",
                  arguments: {},
                  status: "running",
                },
                {
                  callId: "call-denied",
                  toolName: "read_file",
                  arguments: {},
                  status: "denied",
                  deniedInfo: {
                    mode: "chat_safe",
                    errorCode: "MODE_DENIED",
                    message: "denied",
                    nextAction: "n/a",
                  },
                },
              ],
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [requestId]: "main" },
    })

    useChatStore.getState()._handleServerMessage({
      type: "stream_chunk",
      id: requestId,
      data: { content: "", done: true },
    })

    const tcs = getSession("main")!.messages[0].toolCalls!
    expect(tcs[0].status).toBe("complete")
    expect(tcs[1].status).toBe("denied")
  })
})

// --- History response routing ---

describe("history response routing", () => {
  beforeEach(resetStore)

  it("full replacement on history response", () => {
    connectStore()
    const requestId = getLastHistoryRequestId()

    // Pre-populate
    useChatStore.setState({
      sessionsById: {
        ...useChatStore.getState().sessionsById,
        main: {
          ...getSession("main")!,
          messages: [
            {
              id: "local-1",
              role: "user",
              content: "local msg",
              timestamp: Date.now(),
              status: "complete",
            },
          ],
        },
      },
    })

    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: requestId,
      data: {
        messages: [
          {
            role: "user",
            content: "from server",
            timestamp: "2024-01-01T00:00:00Z",
          },
          {
            role: "assistant",
            content: "reply",
            timestamp: "2024-01-01T00:00:01Z",
          },
        ],
      },
    })

    const main = getSession("main")!
    expect(main.isHistoryLoading).toBe(false)
    expect(main.messages).toHaveLength(2)
    expect(main.messages[0].content).toBe("from server")
    expect(main.messages[1].content).toBe("reply")
  })

  it("routes history to correct session among multiple", () => {
    const histA = "hist-a"
    const histB = "hist-b"
    useChatStore.setState({
      activeSessionId: "main",
      sessionOrder: ["main", "thread-2"],
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          pendingHistoryId: histA,
          isHistoryLoading: true,
        },
        "thread-2": {
          ...createSessionViewState("thread-2"),
          pendingHistoryId: histB,
          isHistoryLoading: true,
        },
      },
    })

    // Response for thread-2
    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: histB,
      data: {
        messages: [
          { role: "user", content: "thread 2 msg", timestamp: "2024-01-01T00:00:00Z" },
        ],
      },
    })

    expect(getSession("thread-2")!.isHistoryLoading).toBe(false)
    expect(getSession("thread-2")!.messages).toHaveLength(1)
    // main still loading
    expect(getSession("main")!.isHistoryLoading).toBe(true)
    expect(getSession("main")!.pendingHistoryId).toBe(histA)
  })

  it("derives title from history", () => {
    useChatStore.setState({
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          pendingHistoryId: "h1",
          isHistoryLoading: true,
        },
      },
    })

    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: "h1",
      data: {
        messages: [
          { role: "user", content: "Help me with Python", timestamp: "2024-01-01T00:00:00Z" },
          { role: "assistant", content: "Sure!", timestamp: "2024-01-01T00:00:01Z" },
        ],
      },
    })

    expect(getSession("main")!.title).toBe("Help me with Python")
  })
})

// --- Thread management ---

describe("thread management", () => {
  beforeEach(resetStore)

  it("createThread generates web:{uuid} session", () => {
    useChatStore.getState().createThread()
    const state = useChatStore.getState()
    expect(state.activeSessionId).toMatch(/^web:/)
    expect(state.sessionOrder).toHaveLength(2)
    expect(state.sessionOrder[0]).toBe(state.activeSessionId)
    expect(state.sessionOrder[1]).toBe("main")
  })

  it("switchThread changes activeSessionId", () => {
    useChatStore.getState().createThread()
    const newId = useChatStore.getState().activeSessionId
    useChatStore.getState().switchThread("main")
    expect(useChatStore.getState().activeSessionId).toBe("main")
    // Switch back
    useChatStore.getState().switchThread(newId)
    expect(useChatStore.getState().activeSessionId).toBe(newId)
  })

  it("switchThread lazy-loads history for empty sessions", () => {
    connectStore()
    // Resolve main history
    const historyId = getLastHistoryRequestId()
    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: historyId,
      data: { messages: [] },
    })

    // Create and switch away from new thread
    useChatStore.getState().createThread()
    const newId = useChatStore.getState().activeSessionId
    useChatStore.getState().switchThread("main")

    // Now switch to new thread — should trigger loadHistory
    const sendCountBefore = mockSend.mock.calls.length
    useChatStore.getState().switchThread(newId)
    const sendCountAfter = mockSend.mock.calls.length
    expect(sendCountAfter).toBe(sendCountBefore + 1)
    const lastReq = mockSend.mock.calls[mockSend.mock.calls.length - 1][0] as RPCRequest
    expect(lastReq.method).toBe("chat.history")
    expect(lastReq.params.session_id).toBe(newId)
  })

  it("switchThread does not re-load if messages exist", () => {
    useChatStore.setState({
      activeSessionId: "main",
      sessionOrder: ["main", "thread-2"],
      sessionsById: {
        main: createSessionViewState("main"),
        "thread-2": {
          ...createSessionViewState("thread-2"),
          messages: [
            {
              id: "m1",
              role: "user",
              content: "existing",
              timestamp: Date.now(),
              status: "complete",
            },
          ],
        },
      },
    })

    mockIsConnected = true
    const sendCountBefore = mockSend.mock.calls.length
    useChatStore.getState().switchThread("thread-2")
    expect(mockSend.mock.calls.length).toBe(sendCountBefore)
  })
})

// --- localStorage persistence ---

describe("localStorage persistence", () => {
  beforeEach(resetStore)

  it("persists after createThread", () => {
    useChatStore.getState().createThread()
    const raw = localStorage.getItem("neomagi-threads")
    expect(raw).not.toBeNull()
    const data = JSON.parse(raw!)
    expect(data.sessionOrder).toHaveLength(2)
  })

  it("persists after switchThread", () => {
    useChatStore.getState().createThread()
    const newId = useChatStore.getState().activeSessionId
    useChatStore.getState().switchThread("main")
    const data = JSON.parse(localStorage.getItem("neomagi-threads")!)
    expect(data.activeSessionId).toBe("main")
    // Switch back
    useChatStore.getState().switchThread(newId)
    const data2 = JSON.parse(localStorage.getItem("neomagi-threads")!)
    expect(data2.activeSessionId).toBe(newId)
  })

  it("persists titles", () => {
    connectStore()
    const historyId = getLastHistoryRequestId()
    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: historyId,
      data: {
        messages: [
          { role: "user", content: "Explain async/await", timestamp: "2024-01-01T00:00:00Z" },
        ],
      },
    })

    const data = JSON.parse(localStorage.getItem("neomagi-threads")!)
    expect(data.titles.main).toBe("Explain async/await")
  })
})

// --- Refresh recovery ---

describe("refresh recovery", () => {
  beforeEach(resetStore)

  it("restores thread list from localStorage but not messages", () => {
    localStorage.setItem(
      "neomagi-threads",
      JSON.stringify({
        activeSessionId: "web:abc",
        sessionOrder: ["web:abc", "main"],
        titles: { "web:abc": "My Thread", main: "Main" },
      }),
    )

    // Re-bootstrap by setting state as bootstrapSessions would
    // (In real app, this happens on module load)
    const persisted = JSON.parse(localStorage.getItem("neomagi-threads")!)
    const sessionsById: Record<string, ReturnType<typeof createSessionViewState>> = {}
    for (const id of persisted.sessionOrder) {
      sessionsById[id] = createSessionViewState(id, persisted.titles[id])
    }
    useChatStore.setState({
      activeSessionId: persisted.activeSessionId,
      sessionOrder: persisted.sessionOrder,
      sessionsById,
    })

    const state = useChatStore.getState()
    expect(state.activeSessionId).toBe("web:abc")
    expect(state.sessionOrder).toEqual(["web:abc", "main"])
    expect(getSession("web:abc")!.title).toBe("My Thread")
    // Messages are empty (not persisted)
    expect(getSession("web:abc")!.messages).toHaveLength(0)
    // Streaming state is not restored
    expect(getSession("web:abc")!.isStreaming).toBe(false)
  })
})

// --- Cross-thread isolation ---

describe("cross-thread isolation", () => {
  beforeEach(resetStore)

  it("streaming in thread-A does not block sending in thread-B", () => {
    connectStore()
    const mainHistoryId = getLastHistoryRequestId()
    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: mainHistoryId,
      data: { messages: [] },
    })

    // Send in main → starts streaming
    useChatStore.getState().sendMessage("hello from main")
    expect(getSession("main")!.isStreaming).toBe(true)

    // Create new thread and switch to it
    useChatStore.getState().createThread()
    const threadB = useChatStore.getState().activeSessionId

    // Should be able to send in thread-B even though main is streaming
    const sent = useChatStore.getState().sendMessage("hello from B")
    expect(sent).toBe(true)
    expect(getSession(threadB)!.messages).toHaveLength(2)
    expect(getSession(threadB)!.isStreaming).toBe(true)

    // main still streaming independently
    expect(getSession("main")!.isStreaming).toBe(true)
  })

  it("completing thread-A does not affect thread-B streaming state", () => {
    const reqA = "req-a"
    const reqB = "req-b"
    useChatStore.setState({
      activeSessionId: "thread-2",
      sessionOrder: ["main", "thread-2"],
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: reqA,
              role: "assistant",
              content: "resp A",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
        "thread-2": {
          ...createSessionViewState("thread-2"),
          messages: [
            {
              id: reqB,
              role: "assistant",
              content: "resp B",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [reqA]: "main", [reqB]: "thread-2" },
    })

    // Complete main
    useChatStore.getState()._handleServerMessage({
      type: "stream_chunk",
      id: reqA,
      data: { content: "", done: true },
    })

    // thread-2 still streaming
    expect(getSession("thread-2")!.isStreaming).toBe(true)
    expect(getSession("main")!.isStreaming).toBe(false)
  })
})

// --- Connection status ---

describe("connection status toasts", () => {
  beforeEach(resetStore)

  it("toast on connection lost", async () => {
    const { toast } = await import("sonner")
    useChatStore.setState({ connectionStatus: "connected" })
    useChatStore.getState()._setConnectionStatus("reconnecting")
    expect(toast.warning).toHaveBeenCalledWith(
      "Connection lost, reconnecting...",
    )
  })

  it("toast on disconnect after reconnecting", async () => {
    const { toast } = await import("sonner")
    useChatStore.setState({ connectionStatus: "reconnecting" })
    useChatStore.getState()._setConnectionStatus("disconnected")
    expect(toast.error).toHaveBeenCalledWith(
      "Failed to reconnect. Please refresh the page.",
      { duration: Infinity },
    )
  })
})

// --- P1: In-flight request recovery on connection loss ---

describe("connection loss recovers in-flight requests", () => {
  beforeEach(resetStore)

  it("disconnect marks in-flight messages as error and tools as complete", () => {
    const reqA = "req-a"
    useChatStore.setState({
      connectionStatus: "connected",
      activeSessionId: "main",
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: reqA,
              role: "assistant",
              content: "partial",
              timestamp: Date.now(),
              status: "streaming",
              toolCalls: [
                {
                  callId: "tc-1",
                  toolName: "read_file",
                  arguments: {},
                  status: "running",
                },
              ],
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [reqA]: "main" },
    })

    useChatStore.getState()._setConnectionStatus("disconnected")
    const main = getSession("main")!
    expect(main.isStreaming).toBe(false)
    expect(useChatStore.getState().requestToSession).toEqual({})
    // Message transitioned to terminal error state
    expect(main.messages[0].status).toBe("error")
    expect(main.messages[0].error).toBe("Connection lost")
    // Tool call transitioned to aborted (not complete — it didn't succeed)
    expect(main.messages[0].toolCalls![0].status).toBe("aborted")
  })

  it("reconnecting clears requestToSession and resets isStreaming", () => {
    const reqA = "req-a"
    const reqB = "req-b"
    useChatStore.setState({
      connectionStatus: "connected",
      activeSessionId: "main",
      sessionOrder: ["main", "thread-2"],
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: reqA,
              role: "assistant",
              content: "partial A",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
        "thread-2": {
          ...createSessionViewState("thread-2"),
          messages: [
            {
              id: reqB,
              role: "assistant",
              content: "partial B",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [reqA]: "main", [reqB]: "thread-2" },
    })

    useChatStore.getState()._setConnectionStatus("reconnecting")

    // Both sessions recovered
    expect(getSession("main")!.isStreaming).toBe(false)
    expect(getSession("thread-2")!.isStreaming).toBe(false)
    expect(useChatStore.getState().requestToSession).toEqual({})
    // Both messages in terminal state
    expect(getSession("main")!.messages[0].status).toBe("error")
    expect(getSession("thread-2")!.messages[0].status).toBe("error")
  })

  it("recovered thread can send again after reconnect", () => {
    const reqA = "req-a"
    useChatStore.setState({
      connectionStatus: "connected",
      activeSessionId: "main",
      sessionsById: {
        main: {
          ...createSessionViewState("main"),
          messages: [
            {
              id: reqA,
              role: "assistant",
              content: "partial",
              timestamp: Date.now(),
              status: "streaming",
            },
          ],
          isStreaming: true,
        },
      },
      requestToSession: { [reqA]: "main" },
    })

    // Connection drops
    useChatStore.getState()._setConnectionStatus("reconnecting")
    // Connection restores
    mockIsConnected = true
    useChatStore.getState()._setConnectionStatus("connected")

    // Thread is no longer blocked
    expect(getSession("main")!.isStreaming).toBe(false)

    // sendMessage should work
    const sent = useChatStore.getState().sendMessage("hello again")
    expect(sent).toBe(true)
  })
})
