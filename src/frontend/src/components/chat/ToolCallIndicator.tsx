import { useState } from "react"
import type { ToolCall } from "@/stores/chat"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { cn } from "@/lib/utils"

interface ToolCallIndicatorProps {
  toolCall: ToolCall
}

export function ToolCallIndicator({ toolCall }: ToolCallIndicatorProps) {
  const [open, setOpen] = useState(false)
  const isRunning = toolCall.status === "running"
  const hasArgs = Object.keys(toolCall.arguments).length > 0

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors",
          "hover:bg-background/50",
          isRunning ? "text-muted-foreground" : "text-muted-foreground/70"
        )}
      >
        {isRunning ? (
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" />
        ) : (
          <svg
            className="h-3 w-3 text-green-500"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={3}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        )}
        <span>
          {isRunning ? `Calling ${toolCall.toolName}...` : toolCall.toolName}
        </span>
        {hasArgs && (
          <svg
            className={cn(
              "ml-auto h-3 w-3 transition-transform",
              open && "rotate-180"
            )}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        )}
      </CollapsibleTrigger>
      {hasArgs && (
        <CollapsibleContent>
          <pre className="mt-1 overflow-x-auto rounded bg-background/50 p-2 text-xs text-muted-foreground">
            {JSON.stringify(toolCall.arguments, null, 2)}
          </pre>
        </CollapsibleContent>
      )}
    </Collapsible>
  )
}
