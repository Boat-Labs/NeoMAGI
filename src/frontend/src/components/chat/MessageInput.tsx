import { useState, useCallback, type KeyboardEvent } from "react"
import { Button } from "@/components/ui/button"
import { useChatStore } from "@/stores/chat"

export function MessageInput() {
  const [input, setInput] = useState("")
  const sendMessage = useChatStore((state) => state.sendMessage)
  const isStreaming = useChatStore((state) => state.isStreaming)
  const connectionStatus = useChatStore((state) => state.connectionStatus)

  const isDisabled = isStreaming || connectionStatus !== "connected"
  const canSend = !isDisabled && input.trim().length > 0

  const handleSend = useCallback(() => {
    const trimmed = input.trim()
    if (!trimmed || isDisabled) return
    sendMessage(trimmed)
    setInput("")
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

  return (
    <div className="flex items-end gap-2 border-t p-4">
      <textarea
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={isDisabled ? "Waiting for connection..." : "Type a message..."}
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
