import { createBrowserClient } from '@supabase/ssr'
import type { SupabaseClient } from '@supabase/supabase-js'

const url = process.env.NEXT_PUBLIC_SUPABASE_URL
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY

/**
 * The studio runs against the reconstruction engine alone; Supabase only adds
 * the sign-in gate. Scaffold placeholders count as "not configured" so a fresh
 * checkout never dies inside the auth client.
 */
export const isSupabaseConfigured =
  Boolean(url) &&
  url !== 'https://placeholder.supabase.co' &&
  Boolean(anonKey) &&
  anonKey !== 'placeholder_anon_key'

let browserClient: SupabaseClient | null = null

export const createClient = (): SupabaseClient | null => {
  if (!isSupabaseConfigured || !url || !anonKey) return null
  if (!browserClient) browserClient = createBrowserClient(url, anonKey)
  return browserClient
}
