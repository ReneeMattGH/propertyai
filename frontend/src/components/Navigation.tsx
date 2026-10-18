'use client'

import { createClient } from '@/lib/supabase/client'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

export default function Navigation() {
  const [user, setUser] = useState<any>(null)
  const [mounted, setMounted] = useState(false)
  const [supabaseConfigured, setSupabaseConfigured] = useState(false)
  const router = useRouter()
  
  // Only create supabase client after component mounts
  const supabase = mounted ? createClient() : null

  useEffect(() => {
    setMounted(true)
    
    // Check if Supabase is configured
    const hasUrl = process.env.NEXT_PUBLIC_SUPABASE_URL && 
                   process.env.NEXT_PUBLIC_SUPABASE_URL !== 'https://placeholder.supabase.co'
    const hasKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY && 
                   process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY !== 'placeholder_anon_key'
    setSupabaseConfigured(hasUrl && hasKey)
    
    if (!supabase || !supabaseConfigured) return

    const getUser = async () => {
      try {
        const { data: { user } } = await supabase.auth.getUser()
        setUser(user)
      } catch (error) {
        console.error('Error getting user:', error)
      }
    }
    getUser()

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null)
    })

    return () => subscription.unsubscribe()
  }, [supabase])

  const handleSignOut = async () => {
    if (!supabase) return
    await supabase.auth.signOut()
    router.push('/')
    router.refresh()
  }

  return (
    <nav className="bg-gray-900 border-b border-gray-800">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16">
          <div className="flex">
            <Link href="/" className="flex items-center">
              <span className="text-xl font-bold text-white">RealEstAI</span>
            </Link>
            <div className="hidden sm:ml-6 sm:flex sm:space-x-8">
              <Link
                href="/dashboard"
                className="border-transparent text-gray-300 hover:border-gray-500 hover:text-white inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium"
              >
                Dashboard
              </Link>
              <Link
                href="/properties"
                className="border-transparent text-gray-300 hover:border-gray-500 hover:text-white inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium"
              >
                Properties
              </Link>
              <Link
                href="/create-tour"
                className="border-transparent text-gray-300 hover:border-gray-500 hover:text-white inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium"
              >
                Create Tour
              </Link>
            </div>
          </div>
          <div className="flex items-center">
            {!supabaseConfigured ? (
              <div className="bg-yellow-900/50 border border-yellow-500 text-yellow-200 px-3 py-1 rounded text-sm">
                Demo Mode (No Auth)
              </div>
            ) : user ? (
              <div className="flex items-center space-x-4">
                <span className="text-sm text-gray-300">{user.email}</span>
                <button
                  onClick={handleSignOut}
                  className="text-gray-300 hover:text-white text-sm font-medium"
                >
                  Sign out
                </button>
              </div>
            ) : (
              <div className="flex items-center space-x-4">
                <Link
                  href="/auth/signin"
                  className="text-gray-300 hover:text-white text-sm font-medium"
                >
                  Sign in
                </Link>
                <Link
                  href="/auth/signup"
                  className="bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-blue-700"
                >
                  Sign up
                </Link>
              </div>
            )}
          </div>
        </div>
      </div>
    </nav>
  )
}
