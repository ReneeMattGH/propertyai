'use client'

import { createClient } from '@/lib/supabase/client'
import { useIsClient } from '@/lib/useIsClient'
import type { User } from '@supabase/supabase-js'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'

const supabaseConfigured =
  Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL) &&
  process.env.NEXT_PUBLIC_SUPABASE_URL !== 'https://placeholder.supabase.co' &&
  Boolean(process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY) &&
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY !== 'placeholder_anon_key'

const links = [
  { href: '/dashboard', label: 'Dashboard', match: ['/dashboard'] },
  { href: '/tours', label: 'Walkthroughs', match: ['/tours', '/tour'] },
] as const

function Wordmark() {
  return (
    <span className="flex items-center gap-2.5">
      <span className="relative grid h-9 w-9 place-items-center rounded-[10px] bg-forest text-ivory shadow-[0_1px_0_rgba(255,255,255,0.12)_inset]">
        <svg viewBox="0 0 24 24" className="h-[19px] w-[19px]" aria-hidden="true">
          <path
            d="M4 9.2 12 3.5l8 5.7V20h-5.6v-5.4H9.6V20H4V9.2Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinejoin="round"
          />
          <path d="M4 12.4h5.6M14.4 12.4H20" stroke="currentColor" strokeWidth="1.1" />
        </svg>
      </span>
      <span className="flex flex-col leading-none">
        <span className="font-serif text-[19px] font-medium tracking-tight text-ink">
          RealEst<span className="text-bronze">AI</span>
        </span>
        <span className="mt-1 text-[9px] font-semibold uppercase tracking-[0.24em] text-stone-light">
          Spatial Studio
        </span>
      </span>
    </span>
  )
}

export default function Navigation() {
  const [user, setUser] = useState<User | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const isClient = useIsClient()
  const router = useRouter()
  const pathname = usePathname()
  const supabase = useMemo(
    () => (isClient && supabaseConfigured ? createClient() : null),
    [isClient],
  )

  useEffect(() => {
    if (!supabase) return
    let active = true
    supabase.auth
      .getUser()
      .then(({ data }) => {
        if (active) setUser(data.user)
      })
      .catch((error: unknown) => {
        console.error('Error getting user:', error)
      })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null)
    })

    return () => {
      active = false
      subscription.unsubscribe()
    }
  }, [supabase])

  const handleSignOut = async () => {
    setMenuOpen(false)
    if (!supabase) return
    await supabase.auth.signOut()
    router.push('/')
    router.refresh()
  }

  const initial = user?.email?.charAt(0).toUpperCase() ?? null

  return (
    <header className="sticky top-0 z-50 border-b border-line/80 bg-ivory/85 backdrop-blur-md">
      <nav className="shell flex h-[68px] items-center justify-between gap-4" aria-label="Main">
        <Link href="/" className="shrink-0 rounded-md" aria-label="RealEstAI home">
          <Wordmark />
        </Link>

        <div className="hidden items-center gap-1 md:flex">
          {links.map((link) => {
            const active = link.match.some(
              (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
            )
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? 'page' : undefined}
                className={`relative rounded-md px-3.5 py-2 text-[13.5px] transition-colors ${
                  active
                    ? 'font-medium text-ink'
                    : 'text-stone hover:bg-ink/[0.04] hover:text-ink'
                }`}
              >
                {link.label}
                <span
                  className={`absolute inset-x-3.5 -bottom-[3px] h-[2px] rounded-full bg-bronze transition-opacity ${
                    active ? 'opacity-100' : 'opacity-0'
                  }`}
                />
              </Link>
            )
          })}
        </div>

        <div className="hidden items-center gap-3 md:flex">
          {!supabaseConfigured ? (
            <span className="chip chip-neutral" title="Authentication is not configured for this local workspace">
              <span className="h-1.5 w-1.5 rounded-full bg-bronze" />
              Local workspace
            </span>
          ) : user ? (
            <div className="flex items-center gap-3">
              <span
                className="grid h-8 w-8 place-items-center rounded-full border border-line-strong bg-parchment text-xs font-semibold text-forest"
                title={user.email ?? 'Signed in'}
              >
                {initial ?? '·'}
              </span>
              <div className="hidden max-w-[160px] flex-col leading-tight lg:flex">
                <span className="truncate text-xs font-medium text-ink">{user.email}</span>
                <button
                  onClick={handleSignOut}
                  className="mt-0.5 text-left text-[11px] text-stone hover:text-ink"
                >
                  Sign out
                </button>
              </div>
            </div>
          ) : (
            <Link href="/auth/signin" className="btn btn-ghost btn-sm">
              Sign in
            </Link>
          )}
          <Link href="/create-tour" className="btn btn-primary btn-sm">
            <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
              <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
            New walkthrough
          </Link>
        </div>

        <div className="flex items-center gap-2 md:hidden">
          <Link href="/create-tour" onClick={() => setMenuOpen(false)} className="btn btn-primary btn-sm">
            New
          </Link>
          <button
            type="button"
            onClick={() => setMenuOpen((value) => !value)}
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            className="grid h-9 w-9 place-items-center rounded-[8px] border border-line-strong bg-surface text-ink"
          >
            {menuOpen ? (
              <svg viewBox="0 0 20 20" className="h-4 w-4" aria-hidden="true">
                <path d="m5 5 10 10M15 5 5 15" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            ) : (
              <svg viewBox="0 0 20 20" className="h-4 w-4" aria-hidden="true">
                <path d="M4 6h12M4 10h12M4 14h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            )}
          </button>
        </div>
      </nav>

      {menuOpen && (
        <div id="mobile-nav" className="animate-fade border-t border-line bg-surface md:hidden">
          <div className="shell flex flex-col gap-1 py-3">
            {links.map((link) => {
              const active = link.match.some(
                (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
              )
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => setMenuOpen(false)}
                  aria-current={active ? 'page' : undefined}
                  className={`rounded-lg px-3 py-2.5 text-sm ${
                    active ? 'bg-parchment font-medium text-ink' : 'text-stone'
                  }`}
                >
                  {link.label}
                </Link>
              )
            })}
            <div className="my-2 h-px bg-line" />
            {!supabaseConfigured ? (
              <span className="px-3 py-1 text-xs text-stone">Local workspace · no authentication</span>
            ) : user ? (
              <button
                onClick={handleSignOut}
                className="rounded-lg px-3 py-2.5 text-left text-sm text-stone hover:bg-parchment"
              >
                Sign out ({user.email})
              </button>
            ) : (
              <Link
                href="/auth/signin"
                onClick={() => setMenuOpen(false)}
                className="rounded-lg px-3 py-2.5 text-sm text-stone"
              >
                Sign in
              </Link>
            )}
          </div>
        </div>
      )}
    </header>
  )
}