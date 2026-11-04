'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { AuthAlert, AuthField, AuthShell } from '@/components/AuthShell'
import { createClient } from '@/lib/supabase/client'
import { useIsClient } from '@/lib/useIsClient'

export default function SignUpPage() {
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const isClient = useIsClient()
  const router = useRouter()
  const supabase = isClient ? createClient() : null

  const handleSignUp = async (event: React.FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError(null)
    setMessage(null)

    if (!supabase) {
      setError('Sign-up is unavailable because Supabase is not configured for this deployment.')
      setLoading(false)
      return
    }

    const { data, error: signUpError } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { full_name: fullName } },
    })

    if (signUpError) {
      setError(signUpError.message)
      setLoading(false)
      return
    }

    if (data.user && !data.session) {
      setMessage('Check your inbox for the confirmation link.')
    } else if (data.session) {
      router.push('/dashboard')
      router.refresh()
    }

    setLoading(false)
  }

  return (
    <AuthShell
      eyebrow="Studio access"
      title="Create your account"
      subtitle="For studios running Crestion Labs with more than one operator on the machine."
      footer={
        <>
          Already registered?{' '}
          <Link href="/auth/signin" className="font-medium text-forest underline decoration-line-strong underline-offset-4 hover:text-ink">
            Sign in
          </Link>
        </>
      }
    >
      {error && <AuthAlert tone="error">{error}</AuthAlert>}
      {message && <AuthAlert tone="success">{message}</AuthAlert>}

      <form className="space-y-5" onSubmit={handleSignUp}>
        <AuthField
          id="fullName"
          name="fullName"
          label="Full name"
          type="text"
          autoComplete="name"
          required
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
          placeholder="Your name"
        />
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
          autoComplete="new-password"
          required
          minLength={6}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="At least 6 characters"
          hint="Minimum 6 characters."
        />
        <button type="submit" className="btn btn-primary w-full" disabled={loading}>
          {loading ? 'Creating account…' : 'Create account'}
        </button>
      </form>
    </AuthShell>
  )
}
