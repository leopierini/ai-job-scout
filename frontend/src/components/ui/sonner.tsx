import { Toaster as Sonner, type ToasterProps } from "sonner"
import { CircleCheckIcon, InfoIcon, TriangleAlertIcon, OctagonXIcon, Loader2Icon } from "lucide-react"

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="light" // light theme only in v1 (DESIGN.md)
      className="toaster group"
      icons={{
        success: (
          <CircleCheckIcon className="size-4" />
        ),
        info: (
          <InfoIcon className="size-4" />
        ),
        warning: (
          <TriangleAlertIcon className="size-4" />
        ),
        error: (
          <OctagonXIcon className="size-4" />
        ),
        loading: (
          <Loader2Icon className="size-4 animate-spin" />
        ),
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
          fontFamily: "var(--font-sans)", // sonner sets its own system font stack otherwise
        } as React.CSSProperties
      }
      toastOptions={{
        classNames: {
          toast: "cn-toast",
          // Sonner styles its buttons as filled text-colour pills; actions (Undo) use the outline button instead.
          actionButton:
            "h-8! rounded-md! border! border-border! bg-card! px-2.5! text-sm! font-medium! text-foreground! hover:bg-neutral-soft! focus-visible:border-ring! focus-visible:ring-3! focus-visible:ring-ring!",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
