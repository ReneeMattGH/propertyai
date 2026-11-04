import type { ReactNode } from 'react'
import { Wordmark } from '@/components/Wordmark'

export function AuthShell({
  eyebrow,
  title,
  subtitle,
  children,
  footer,
}: {
  eyebrow: string
  title: string
  subtitle: string
  children: ReactNode
  footer: ReactNode
}) {
  return (
    <main className="flex min-h-screen flex-col justify-center bg-ivory px-5 py-14">
      <div className="shell">
        <div className="mx-auto w-full max-w-[440px]">
          <Wordmark tagline="Studio access" />
          <div className="card mt-7 p-6 shadow-lift sm:p-7">
            <p className="eyebrow">{eyebrow}</p>
            <h1 className="mt-3 text-[26px] leading-tight text-ink">{title}</h1>
            <p className="mt-2 text-[13px] leading-6 text-stone">{subtitle}</p>
            <div className="mt-6">{children}</div>
          </div>
          <p className="mt-5 text-center text-[12.5px] leading-5 text-stone">{footer}</p>
        </div>
      </div>
    </main>
  )
}

export function AuthAlert({ tone, children }: { tone: 'error' | 'success'; children: ReactNode }) {
  const styles =
    tone === 'error'
      ? 'border-[rgba(158,70,58,0.35)] bg-[rgba(158,70,58,0.08)] text-danger'
      : 'border-[rgba(61,106,79,0.35)] bg-[rgba(61,106,79,0.08)] text-success'
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={`mb-5 rounded-[10px] border px-4 py-3 text-[12.5px] leading-5 ${styles}`}>
      {children}
    </div>
  )
}

export function AuthField({
  id,
  label,
  hint,
  ...input
}: React.InputHTMLAttributes<HTMLInputElement> & { id: string; label: string; hint?: string }) {
  return (
    <div>
      <label htmlFor={id} className="label">
        {label}
      </label>
      <input id={id} className="field" {...input} />
      {hint && <p className="mt-1.5 text-[11.5px] leading-4 text-stone-light">{hint}</p>}
    </div>
  )
}
