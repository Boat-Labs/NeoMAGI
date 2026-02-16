import type { ChatMessage } from "@/stores/chat"
import { cn } from "@/lib/utils"

interface MessageBubbleProps {
  message: ChatMessage
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user"

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[75%] rounded-2xl px-4 py-2",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-foreground",
          message.status === "error" && "border border-destructive"
        )}
      >
        <p className="whitespace-pre-wrap break-words text-sm">
          {message.content}
          {message.status === "streaming" && (
            <span className="ml-1 inline-block h-4 w-1.5 animate-pulse bg-current" />
          )}
        </p>
        {message.status === "error" && message.error && (
          <p className="mt-1 text-xs text-destructive">{message.error}</p>
        )}
      </div>
    </div>
  )
}
