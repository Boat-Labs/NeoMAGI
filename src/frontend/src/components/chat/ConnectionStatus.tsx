import { useEffect, useState } from "react"
import { useChatStore } from "@/stores/chat"
import { cn } from "@/lib/utils"

const BANNER_CONFIG = {
  connecting: {
    bg: "bg-yellow-500/10 border-yellow-500/30",
    dot: "bg-yellow-500",
    text: "text-yellow-700 dark:text-yellow-400",
    label: "Connecting...",
  },
  reconnecting: {
    bg: "bg-orange-500/10 border-orange-500/30",
    dot: "bg-orange-500",
    text: "text-orange-700 dark:text-orange-400",
    label: "Connection lost. Reconnecting...",
  },
  disconnected: {
    bg: "bg-red-500/10 border-red-500/30",
    dot: "bg-red-500",
    text: "text-red-700 dark:text-red-400",
    label: "Disconnected. Please check your connection.",
  },
} as const

export function ConnectionStatus() {
  const connectionStatus = useChatStore((state) => state.connectionStatus)
  const [showConnected, setShowConnected] = useState(false)

  // Briefly show "Connected" then fade out
  useEffect(() => {
    if (connectionStatus === "connected") {
      setShowConnected(true)
      const timer = setTimeout(() => setShowConnected(false), 2000)
      return () => clearTimeout(timer)
    }
    setShowConnected(false)
  }, [connectionStatus])

  if (connectionStatus === "connected") {
    if (!showConnected) return null
    return (
      <div className="flex items-center gap-2 border-b border-green-500/30 bg-green-500/10 px-4 py-1.5 text-xs text-green-700 transition-opacity dark:text-green-400">
        <span className="h-2 w-2 rounded-full bg-green-500" />
        Connected
      </div>
    )
  }

  const config = BANNER_CONFIG[connectionStatus]
  if (!config) return null

  return (
    <div
      className={cn(
        "flex items-center gap-2 border-b px-4 py-1.5 text-xs",
        config.bg,
        config.text
      )}
    >
      <span className={cn("h-2 w-2 rounded-full", config.dot)} />
      {config.label}
    </div>
  )
}
