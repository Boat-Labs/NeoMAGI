import { ScrollArea } from "@/components/ui/scroll-area"
import { useChatStore } from "@/stores/chat"
import { MessageBubble } from "./MessageBubble"
import { useAutoScroll } from "@/hooks/useAutoScroll"

export function MessageList() {
  const messages = useChatStore((state) => state.messages)
  const bottomRef = useAutoScroll(messages)

  return (
    <ScrollArea className="flex-1">
      <div className="space-y-4 p-4">
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center pt-32 text-muted-foreground">
            <p>Send a message to start chatting.</p>
          </div>
        ) : (
          messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  )
}
