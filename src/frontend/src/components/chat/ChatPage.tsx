import { useEffect } from "react"
import { useChatStore } from "@/stores/chat"
import { ConnectionStatus } from "./ConnectionStatus"
import { MessageList } from "./MessageList"
import { MessageInput } from "./MessageInput"

const WS_URL = "ws://localhost:19789/ws"

export function ChatPage() {
  const connect = useChatStore((state) => state.connect)
  const disconnect = useChatStore((state) => state.disconnect)

  useEffect(() => {
    connect(WS_URL)
    return () => disconnect()
  }, [connect, disconnect])

  return (
    <div className="mx-auto flex h-screen max-w-3xl flex-col border-x bg-background">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <h1 className="text-lg font-semibold">NeoMAGI</h1>
        <ConnectionStatus />
      </header>
      <MessageList />
      <MessageInput />
    </div>
  )
}
