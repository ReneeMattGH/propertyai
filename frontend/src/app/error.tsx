'use client'

import { useEffect } from 'react'
import Link from 'next/link'
import Navigation from '@/components/Navigation'

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error('Unhandled studio error:', error)
  }, [error])

  return (
    <div className="min-h-screen bg-ivory">
      <Navigation />
      <main className="shell py-24">
        <div className="card mx-auto max-w-xl px-8 py-12 text-center">
          <p className="eyebrow">Something broke</p>
          <h1 className="mt-2 text-[30px] leading-tight">This view could not be rendered</h1>
          <p className="mt-3 break-words text-sm leading-6 text-stone">
            {error.message || 'An unexpected error interrupted the page.'}
          </p>
          {error.digest && (
            <p className="mt-2 font-mono text-[11px] text-stone-light">Reference {error.digest}</p>
          )}
          <div className="mt-7 flex flex-wrap justify-center gap-2.5">
            <button type="button" onClick={reset} className="btn btn-primary">
              Try again
            </button>
            <Link href="/dashboard" className="btn btn-secondary">
              Go to dashboard
            </Link>
          </div>
        </div>
      </main>
    </div>
  )
}
