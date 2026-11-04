'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { AuthAlert, AuthField, AuthShell } from '@/components/AuthShell'
import { createClient } from '@/lib/supabase/client'
import { useIsClient } from '@/lib/useIsClient'

export default function SignInPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isClient = useIsClient()
  const router = useRouter()
  const supabase = isClient ? createClient() : null

  const handleSignIn = async (event: React.FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError(null)

    if (!supabase) {
      setError('Sign-in is unavailable because Supabase is not configured for this deployment.')
      setLoading(false)
      return
    }

    const { error: signInError } = await supabase.auth.signInWithPassword({ email, password })
    if (signInError) {
      setError(signInError.message)
      setLoading(false)
      return
    }

    router.push('/dashboard')
    router.refresh()
  }

  if (!supabase) {
    return (
      <AuthShell
        eyebrow="Studio access"
        title="Sign in to Crestion Labs"
        subtitle="Reconstruction runs locally, so the studio opens without a sign-in step."
        footer={<Link href="/dashboard" className="hover:text-ink">Open the studio instead</Link>}
      >
        <AuthAlert tone="success">
          Supabase credentials are not set in <code className="font-mono text-[11.5px]">.env.local</code>,
          so account sign-in is switched off. Every tool below works directly against the engine.
        </AuthAlert>
        <Link href="/dashboard" className="btn btn-primary w-full">
          Continue to the studio
        </Link>
      </AuthShell>
    )
  }

  return (
    <AuthShell
      eyebrow="Studio access"
      title="Sign in to Crestion Labs"
      subtitle="Floor plans, reconstructions and walkthroughs, all produced on this machine."
      footer={
        <>
          Need an account?{' '}
          <Link href="/auth/signup" className="font-medium text-forest underline decoration-line-strong underline-offset-4 hover:text-ink">
            Sign up
          </Link>
        </>
      }
    >
      {error && <AuthAlert tone="error">{error}</AuthAlert>}

      <form className="space-y-5" onSubmit={handleSignIn}>
        <AuthField
          id="email"
          name="email"
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="studio@crestionlabs.com"
        />
        <AuthField
          id="password"
          name="password"
          label="Password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Your password"
        />
        <button type="submit" className="btn btn-primary w-full" disabled={loading}>
          {loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </AuthShell>
  )
}
