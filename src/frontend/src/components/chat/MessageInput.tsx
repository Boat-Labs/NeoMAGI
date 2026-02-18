import { useState, useCallback, useRef, useEffect, type KeyboardEvent } from "react"
import { Button } from "@/components/ui/button"
import { useChatStore } from "@/stores/chat"

const MAX_ROWS = 6
const LINE_HEIGHT = 24 // px approximate

export function MessageInput() {
  const [input, setInput] = useState("")
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const sendMessage = useChatStore((state) => state.sendMessage)
  const isStreaming = useChatStore((state) => state.isStreaming)
  const connectionStatus = useChatStore((state) => state.connectionStatus)
  const isHistoryLoading = useChatStore((state) => state.isHistoryLoading)

  const isDisabled = isStreaming || connectionStatus !== "connected" || isHistoryLoading
  const canSend = !isDisabled && input.trim().length > 0

  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, MAX_ROWS * LINE_HEIGHT)}px`
  }, [])

  const handleSend = useCallback(() => {
    const trimmed = input.trim()
    if (!trimmed || isDisabled) return
    const sent = sendMessage(trimmed)
    if (sent) {
      setInput("")
      const el = textareaRef.current
      if (el) el.style.height = "auto"
    }
  }, [input, isDisabled, sendMessage])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend]
  )

  // Auto-resize on input change
  useEffect(() => {
    resizeTextarea()
  }, [input, resizeTextarea])

  // Auto-focus when streaming ends or connection established
  useEffect(() => {
    if (!isDisabled) {
      textareaRef.current?.focus()
    }
  }, [isDisabled])

  return (
    <div className="flex items-end gap-2 border-t p-4">
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={
          connectionStatus !== "connected"
            ? "Connecting..."
            : isHistoryLoading
              ? "Loading history..."
              : "Type a message..."
        }
        rows={1}
        disabled={isDisabled}
        className="flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
      />
      <Button onClick={handleSend} disabled={!canSend} size="default">
        Send
      </Button>
    </div>
  )
}
