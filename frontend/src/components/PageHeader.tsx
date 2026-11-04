import type { ReactNode } from 'react'

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow: string
  title: string
  subtitle?: string
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-5">
      <div className="animate-rise min-w-0">
        <p className="eyebrow">{eyebrow}</p>
        <h1 className="display mt-2.5 text-[32px] sm:text-[40px]">{title}</h1>
        {subtitle && <p className="mt-3 max-w-2xl text-sm leading-6 text-stone">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2.5">{actions}</div>}
    </header>
  )
}
