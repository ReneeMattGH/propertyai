'use client'

import { useState, useEffect, Suspense } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase/client'
import { useIsClient } from '@/lib/useIsClient'
import Navigation from '@/components/Navigation'

interface Property {
  id: string
  name: string
  address: string
  city: string
  state: string
  zip_code: string
  description: string
  price: number
  bedrooms: number
  bathrooms: number
  square_feet: number
  property_type: string
  listing_status: string
}

function PropertyDetailContent() {
  const params = useParams()
  const router = useRouter()
  const [property, setProperty] = useState<Property | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const isClient = useIsClient()
  const supabase = isClient ? createClient() : null

  useEffect(() => {
    if (!supabase) return

    const loadProperty = async () => {
      try {
        const { data: { user } } = await supabase.auth.getUser()
        if (!user) {
          router.push('/auth/signin')
          return
        }

        const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/properties/${params.id}`, {
          headers: {
            'Authorization': `Bearer ${user.id}`,
          },
        })

        if (!response.ok) {
          throw new Error('Failed to load property')
        }

        const data = await response.json()
        setProperty(data)
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : 'Failed to load property')
      } finally {
        setLoading(false)
      }
    }

    loadProperty()
  }, [params.id, router, supabase])

  const handleDelete = async () => {
    if (!confirm('Are you sure you want to delete this property?')) {
      return
    }

    if (!supabase) return

    try {
      const { data: { user } } = await supabase.auth.getUser()
      if (!user) return

      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/properties/${params.id}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${user.id}`,
        },
      })

      if (!response.ok) {
        throw new Error('Failed to delete property')
      }

      router.push('/dashboard')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete property')
    }
  }

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

  if (error || !property) {
    return (
      <div className="min-h-screen bg-gray-900">
        <Navigation />
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="bg-red-900/50 border border-red-500 text-red-200 px-4 py-3 rounded">
            {error || 'Property not found'}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <Navigation />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="mb-8">
          <button
            onClick={() => router.back()}
            className="text-gray-400 hover:text-white mb-4 inline-block"
          >
            ← Back
          </button>
          <div className="flex justify-between items-start">
            <div>
              <h1 className="text-3xl font-bold text-white">{property.name}</h1>
              <p className="text-gray-400 mt-1">
                {property.address}, {property.city}, {property.state} {property.zip_code}
              </p>
            </div>
            <div className="flex space-x-2">
              <button
                onClick={() => router.push(`/properties/${property.id}/edit`)}
                className="px-4 py-2 bg-gray-700 text-white rounded-lg font-medium hover:bg-gray-600 transition-colors"
              >
                Edit
              </button>
              <button
                onClick={handleDelete}
                className="px-4 py-2 bg-red-600 text-white rounded-lg font-medium hover:bg-red-700 transition-colors"
              >
                Delete
              </button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-2 space-y-6">
            <div className="bg-gray-800 p-6 rounded-lg">
              <h2 className="text-lg font-semibold text-white mb-4">Property Details</h2>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-gray-400 text-sm">Price</p>
                  <p className="text-white font-medium">
                    {property.price ? `$${property.price.toLocaleString()}` : 'Not specified'}
                  </p>
                </div>
                <div>
                  <p className="text-gray-400 text-sm">Property Type</p>
                  <p className="text-white font-medium capitalize">
                    {property.property_type || 'Not specified'}
                  </p>
                </div>
                <div>
                  <p className="text-gray-400 text-sm">Bedrooms</p>
                  <p className="text-white font-medium">{property.bedrooms || '—'}</p>
                </div>
                <div>
                  <p className="text-gray-400 text-sm">Bathrooms</p>
                  <p className="text-white font-medium">{property.bathrooms || '—'}</p>
                </div>
                <div>
                  <p className="text-gray-400 text-sm">Square Feet</p>
                  <p className="text-white font-medium">{property.square_feet ? property.square_feet.toLocaleString() : '—'}</p>
                </div>
                <div>
                  <p className="text-gray-400 text-sm">Status</p>
                  <p className="text-white font-medium capitalize">{property.listing_status}</p>
                </div>
              </div>
            </div>

            <div className="bg-gray-800 p-6 rounded-lg">
              <h2 className="text-lg font-semibold text-white mb-4">Description</h2>
              <p className="text-gray-300">{property.description || 'No description provided.'}</p>
            </div>

            <div className="bg-gray-800 p-6 rounded-lg">
              <h2 className="text-lg font-semibold text-white mb-4">Media</h2>
              <p className="text-gray-400">No media uploaded yet.</p>
              <button 
                onClick={() => router.push(`/properties/${property.id}/upload`)}
                className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition-colors"
              >
                Upload Media
              </button>
            </div>
          </div>

          <div className="space-y-6">
            <div className="bg-gray-800 p-6 rounded-lg">
              <h2 className="text-lg font-semibold text-white mb-4">3D Tour Status</h2>
              <div className="text-center py-8">
                <p className="text-gray-400 mb-4">No tour created yet</p>
                <button className="px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition-colors">
                  Create Tour
                </button>
              </div>
            </div>

            <div className="bg-gray-800 p-6 rounded-lg">
              <h2 className="text-lg font-semibold text-white mb-4">Buyer Inquiries</h2>
              <p className="text-gray-400">No inquiries yet.</p>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}

export default function PropertyDetailPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-gray-900 flex items-center justify-center"><div className="text-white">Loading...</div></div>}>
      <PropertyDetailContent />
    </Suspense>
  )
}
