import { useLayoutEffect, useRef, useState, type ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * A row of tabs that scrolls sideways only when it doesn't fit, without a visible scrollbar. While more is hidden
 * to the right, the right edge fades out, so the cut reads as "scroll for more". A 4px padding inside an equal
 * negative margin leaves room for the focus rings, which the scrolling box would otherwise clip.
 */
export function ScrollStrip({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [moreRight, setMoreRight] = useState(false)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const check = () => setMoreRight(el.scrollWidth - el.clientWidth - el.scrollLeft > 1)
    check()
    const observer = new ResizeObserver(check)
    observer.observe(el)
    if (el.firstElementChild) observer.observe(el.firstElementChild)
    el.addEventListener("scroll", check, { passive: true })
    return () => {
      observer.disconnect()
      el.removeEventListener("scroll", check)
    }
  }, [])

  return (
    <div ref={ref} className={cn("-m-1 min-w-0 overflow-x-auto overflow-y-hidden p-1 no-scrollbar", moreRight && "fade-right", className)}>
      {children}
    </div>
  )
}
