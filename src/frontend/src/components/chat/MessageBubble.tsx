import type { ChatMessage } from "@/stores/chat"
import { cn } from "@/lib/utils"
import { ToolCallIndicator } from "./ToolCallIndicator"

interface MessageBubbleProps {
  message: ChatMessage
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user"
  const hasToolCalls = message.toolCalls && message.toolCalls.length > 0

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
        {message.content && (
          <p className="whitespace-pre-wrap break-words text-sm">
            {message.content}
            {message.status === "streaming" && !hasToolCalls && (
              <span className="ml-1 inline-block h-4 w-1.5 animate-pulse bg-current" />
            )}
          </p>
        )}
        {hasToolCalls && (
          <div className={cn("space-y-1", message.content && "mt-2 border-t border-border/50 pt-2")}>
            {message.toolCalls!.map((tc) => (
              <ToolCallIndicator key={tc.callId} toolCall={tc} />
            ))}
          </div>
        )}
        {message.status === "streaming" && hasToolCalls && !message.content && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" />
            Processing...
          </div>
        )}
        {message.status === "error" && message.error && (
          <p className="mt-1 text-xs text-destructive">{message.error}</p>
        )}
      </div>
    </div>
  )
}
