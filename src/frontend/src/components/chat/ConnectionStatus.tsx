import { useChatStore } from "@/stores/chat"
import { cn } from "@/lib/utils"

const STATUS_CONFIG = {
  connected: { color: "bg-green-500", label: "Connected" },
  connecting: { color: "bg-yellow-500", label: "Connecting..." },
  reconnecting: { color: "bg-orange-500", label: "Reconnecting..." },
  disconnected: { color: "bg-red-500", label: "Disconnected" },
} as const

export function ConnectionStatus() {
  const connectionStatus = useChatStore((state) => state.connectionStatus)
  const config = STATUS_CONFIG[connectionStatus]

  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <span className={cn("h-2 w-2 rounded-full", config.color)} />
      {config.label}
    </div>
  )
}
