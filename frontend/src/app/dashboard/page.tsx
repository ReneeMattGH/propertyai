'use client'

import { createClient } from '@/lib/supabase/client'
import { useIsClient } from '@/lib/useIsClient'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import Navigation from '@/components/Navigation'

interface Property {
  id: string
  name: string
  address: string
  city: string
  state: string
  listing_status: string
  created_at: string
}

export default function DashboardPage() {
  const router = useRouter()
  const [properties] = useState<Property[]>([])
  const [loading, setLoading] = useState(true)
  const isClient = useIsClient()
  const supabase = isClient ? createClient() : null

  useEffect(() => {
    const loadDashboard = async () => {
      try {
        // Check if Supabase is configured
        const hasUrl = process.env.NEXT_PUBLIC_SUPABASE_URL && 
                       process.env.NEXT_PUBLIC_SUPABASE_URL !== 'https://placeholder.supabase.co'
        const hasKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY && 
                       process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY !== 'placeholder_anon_key'
        const supabaseConfigured = hasUrl && hasKey

        if (supabaseConfigured && supabase) {
          const { data: { user } } = await supabase.auth.getUser()
          if (!user) {
            router.push('/auth/signin')
            return
          }
        }

        // Fetch properties from local reconstruction engine or backend API
        // For now, showing empty state
        setLoading(false)
      } catch (error) {
        console.error('Error loading dashboard:', error)
        setLoading(false)
      }
    }

    loadDashboard()
  }, [supabase, router])

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900">
        <Navigation />
        <div className="flex items-center justify-center h-64">
          <div className="text-white">Loading...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <Navigation />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="flex justify-between items-center mb-8">
          <div>
            <h1 className="text-3xl font-bold text-white">Dashboard</h1>
            <p className="text-gray-400 mt-1">Manage your properties and tours</p>
          </div>
          <Link
            href="/properties/new"
            className="bg-blue-600 text-white px-4 py-2 rounded-lg font-medium hover:bg-blue-700 transition-colors"
          >
            Add Property
          </Link>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <div className="bg-gray-800 p-6 rounded-lg">
            <h3 className="text-gray-400 text-sm font-medium mb-2">Total Properties</h3>
            <p className="text-3xl font-bold text-white">{properties.length}</p>
          </div>
          <div className="bg-gray-800 p-6 rounded-lg">
            <h3 className="text-gray-400 text-sm font-medium mb-2">Active Tours</h3>
            <p className="text-3xl font-bold text-white">0</p>
          </div>
          <div className="bg-gray-800 p-6 rounded-lg">
            <h3 className="text-gray-400 text-sm font-medium mb-2">Buyer Inquiries</h3>
            <p className="text-3xl font-bold text-white">0</p>
          </div>
        </div>

        {/* Properties List */}
        <div className="bg-gray-800 rounded-lg">
          <div className="p-6 border-b border-gray-700">
            <h2 className="text-xl font-semibold text-white">Your Properties</h2>
          </div>
          
          {properties.length === 0 ? (
            <div className="p-12 text-center">
              <p className="text-gray-400 mb-4">You haven&apos;t added any properties yet.</p>
              <Link
                href="/properties/new"
                className="text-blue-400 hover:text-blue-300 font-medium"
              >
                Add your first property
              </Link>
            </div>
          ) : (
            <div className="divide-y divide-gray-700">
              {properties.map((property) => (
                <div key={property.id} className="p-6 hover:bg-gray-750 transition-colors">
                  <div className="flex justify-between items-start">
                    <div>
                      <h3 className="text-lg font-medium text-white">{property.name}</h3>
                      <p className="text-gray-400 text-sm mt-1">
                        {property.address}, {property.city}, {property.state}
                      </p>
                      <span className="inline-block mt-2 px-2 py-1 text-xs font-medium rounded bg-gray-700 text-gray-300">
                        {property.listing_status}
                      </span>
                    </div>
                    <div className="flex space-x-2">
                      <Link
                        href={`/properties/${property.id}`}
                        className="text-blue-400 hover:text-blue-300 text-sm font-medium"
                      >
                        View
                      </Link>
                      <Link
                        href={`/properties/${property.id}/edit`}
                        className="text-gray-400 hover:text-white text-sm font-medium ml-4"
                      >
                        Edit
                      </Link>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
