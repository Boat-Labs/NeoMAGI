/**
 * Tests for chat store guard recovery behavior (M1.3 R6f).
 *
 * Strategy: mock WebSocketClient to control isConnected and capture sent
 * messages, then exercise the full loadHistory → error/disconnect flow
 * to verify guard recovery via the actual closure state.
 */
import { describe, it, expect, beforeEach, vi } from "vitest"
import { useChatStore } from "../chat"
import type { RPCRequest } from "@/types/rpc"

// Mock sonner toast to prevent import errors in test environment
vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    warning: vi.fn(),
    success: vi.fn(),
  },
}))

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

function resetStore() {
  // Reset the zustand store's visible state
  useChatStore.setState({
    messages: [],
    connectionStatus: "disconnected",
    isStreaming: false,
    isHistoryLoading: false,
  })
  // Reset mocks
  mockIsConnected = false
  mockSend.mockClear()
  mockClose.mockClear()
  mockConnect.mockClear()
}

/** Connect the store and simulate the WS becoming ready. */
function connectStore() {
  const store = useChatStore.getState()
  store.connect("ws://test")
  mockIsConnected = true
  // Simulate WebSocket open → triggers onConnected → loadHistory
  mockCallbacks.onStatusChange?.("connected")
  mockCallbacks.onConnected?.()
}

/** Get the pendingHistoryId from the last loadHistory call. */
function getLastHistoryRequestId(): string {
  const lastCall = mockSend.mock.calls[mockSend.mock.calls.length - 1]
  const request = lastCall[0] as RPCRequest
  expect(request.method).toBe("chat.history")
  return request.id
}

describe("chat store guard recovery", () => {
  beforeEach(() => {
    resetStore()
  })

  it("clears history guard on error matching pendingHistoryId", () => {
    connectStore()

    // loadHistory was called by onConnected, capture the requestId
    const requestId = getLastHistoryRequestId()
    expect(useChatStore.getState().isHistoryLoading).toBe(true)

    // Simulate server error response with matching ID
    useChatStore.getState()._handleServerMessage({
      type: "error",
      id: requestId,
      error: { code: "INTERNAL_ERROR", message: "something failed" },
    })

    expect(useChatStore.getState().isHistoryLoading).toBe(false)
  })

  it("clears history guard on disconnect", () => {
    connectStore()
    expect(useChatStore.getState().isHistoryLoading).toBe(true)

    // Simulate disconnect
    useChatStore.getState()._setConnectionStatus("disconnected")

    expect(useChatStore.getState().isHistoryLoading).toBe(false)
    expect(useChatStore.getState().connectionStatus).toBe("disconnected")
  })

  it("clears history guard on reconnecting", () => {
    connectStore()
    expect(useChatStore.getState().isHistoryLoading).toBe(true)

    // Simulate reconnecting
    useChatStore.getState()._setConnectionStatus("reconnecting")

    expect(useChatStore.getState().isHistoryLoading).toBe(false)
    expect(useChatStore.getState().connectionStatus).toBe("reconnecting")
  })

  it("clears history guard on timeout", () => {
    vi.useFakeTimers()
    try {
      connectStore()
      expect(useChatStore.getState().isHistoryLoading).toBe(true)

      // Advance time past the 10s timeout
      vi.advanceTimersByTime(10_000)

      expect(useChatStore.getState().isHistoryLoading).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it("timeout does not clear guard if already resolved", () => {
    vi.useFakeTimers()
    try {
      connectStore()
      const requestId = getLastHistoryRequestId()

      // Resolve the history before timeout fires
      useChatStore.getState()._handleServerMessage({
        type: "response",
        id: requestId,
        data: { messages: [] },
      })
      expect(useChatStore.getState().isHistoryLoading).toBe(false)

      // Advance time — timeout fires but should be no-op
      vi.advanceTimersByTime(10_000)
      expect(useChatStore.getState().isHistoryLoading).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it("sendMessage blocked during history loading", () => {
    connectStore()
    expect(useChatStore.getState().isHistoryLoading).toBe(true)

    // sendMessage should return false when history is loading
    const result = useChatStore.getState().sendMessage("hello")
    expect(result).toBe(false)
  })

  it("sendMessage returns false when not connected", () => {
    // wsClient is null (not connected)
    const result = useChatStore.getState().sendMessage("hello")
    expect(result).toBe(false)
  })

  it("full replacement on history response (no duplicates)", () => {
    connectStore()

    const requestId = getLastHistoryRequestId()

    // Pre-populate with local messages to verify replacement
    useChatStore.setState({
      messages: [
        {
          id: "local-1",
          role: "user",
          content: "local message",
          timestamp: Date.now(),
          status: "complete",
        },
      ],
    })

    // Simulate history response with matching ID
    useChatStore.getState()._handleServerMessage({
      type: "response",
      id: requestId,
      data: {
        messages: [
          { role: "user", content: "from server", timestamp: "2024-01-01T00:00:00Z" },
          { role: "assistant", content: "reply from server", timestamp: "2024-01-01T00:00:01Z" },
        ],
      },
    })

    const state = useChatStore.getState()
    expect(state.isHistoryLoading).toBe(false)
    // Full replacement: local message gone, only server messages remain
    expect(state.messages).toHaveLength(2)
    expect(state.messages[0].content).toBe("from server")
    expect(state.messages[1].content).toBe("reply from server")
    expect(state.isStreaming).toBe(false)
  })
})

describe("chat store stream handling", () => {
  beforeEach(() => {
    resetStore()
  })

  it("accumulates content from stream_chunk messages", () => {
    const requestId = "req-1"
    useChatStore.setState({
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
    })

    const store = useChatStore.getState()

    store._handleServerMessage({
      type: "stream_chunk",
      id: requestId,
      data: { content: "Hello ", done: false },
    })
    store._handleServerMessage({
      type: "stream_chunk",
      id: requestId,
      data: { content: "world!", done: false },
    })

    expect(useChatStore.getState().messages[0].content).toBe("Hello world!")
    expect(useChatStore.getState().messages[0].status).toBe("streaming")

    store._handleServerMessage({
      type: "stream_chunk",
      id: requestId,
      data: { content: "", done: true },
    })

    expect(useChatStore.getState().messages[0].status).toBe("complete")
    expect(useChatStore.getState().isStreaming).toBe(false)
  })

  it("adds tool calls to the correct message", () => {
    const requestId = "req-1"
    useChatStore.setState({
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
    })

    useChatStore.getState()._handleServerMessage({
      type: "tool_call",
      id: requestId,
      data: {
        tool_name: "read_file",
        arguments: { path: "test.txt" },
        call_id: "call-1",
      },
    })

    const msg = useChatStore.getState().messages[0]
    expect(msg.toolCalls).toHaveLength(1)
    expect(msg.toolCalls![0].toolName).toBe("read_file")
    expect(msg.toolCalls![0].status).toBe("running")
  })
})
