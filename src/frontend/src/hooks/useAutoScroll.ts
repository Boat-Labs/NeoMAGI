import { useEffect, useRef } from "react"

export function useAutoScroll(dependency: unknown) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [dependency])

  return bottomRef
}
