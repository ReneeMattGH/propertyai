'use client'

import { useState, useEffect, Suspense } from 'react'
import { useParams } from 'next/navigation'
import GameEngine from '@/components/GameEngine'
import Navigation from '@/components/Navigation'

function TourViewerContent() {
  const params = useParams()
  const [property, setProperty] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [showContact, setShowContact] = useState(false)
  const [gameMode, setGameMode] = useState<'game' | 'info'>('game')

  useEffect(() => {
    // Fetch reconstruction job details
    const fetchJobDetails = async () => {
      try {
        const response = await fetch(`http://localhost:8002/reconstructions/${params.id}`)
        if (response.ok) {
          const data = await response.json()
          setProperty({
            id: params.id,
            name: data.project_name || 'Property Tour',
            address: 'Generated from floor plan',
            city: 'Local',
            state: 'NA',
            price: 0,
            bedrooms: 0,
            bathrooms: 0,
            square_feet: 0,
            description: '3D walkthrough generated from floor plan and photographs.',
            jobStatus: data.status,
            diagnostics: data.diagnostics
          })
        }
      } catch (error) {
        console.error('Error fetching job details:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchJobDetails()
  }, [params.id])

  const handleContactSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    alert('Inquiry submitted! The agent will contact you soon.')
    setShowContact(false)
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900">
        <Navigation />
        <div className="flex items-center justify-center h-screen">
          <div className="text-white">Loading tour...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-900 flex flex-col">
      <Navigation />
      
      {/* Game Mode - Fullscreen 3D Experience */}
      {gameMode === 'game' && (
        <div className="flex-1 h-[calc(100vh-64px)] relative">
          <GameEngine jobId={params.id} />
          
          {/* Mode Toggle Button */}
          <button
            onClick={() => setGameMode('info')}
            className="absolute top-4 left-1/2 transform -translate-x-1/2 bg-black/50 text-white px-4 py-2 rounded-lg hover:bg-black/70 transition-colors"
          >
            Property Info
          </button>
        </div>
      )}
      
      {/* Info Mode - Property Details */}
      {gameMode === 'info' && (
        <div className="flex-1 bg-gray-800 p-8">
          <div className="max-w-4xl mx-auto">
            <button
              onClick={() => setGameMode('game')}
              className="mb-6 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
            >
              ← Back to House
            </button>
            
            <h1 className="text-3xl font-bold text-white mb-2">{property?.name}</h1>
            <p className="text-gray-400 mb-6">
              {property?.address}, {property?.city}, {property?.state}
            </p>
            
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <div className="bg-gray-700 p-4 rounded-lg">
                <p className="text-gray-400 text-sm">Status</p>
                <p className="text-white font-medium capitalize">{property?.jobStatus}</p>
              </div>
              <div className="bg-gray-700 p-4 rounded-lg">
                <p className="text-gray-400 text-sm">Floors</p>
                <p className="text-white font-medium">{property?.diagnostics?.validation?.num_floors || 1}</p>
              </div>
              <div className="bg-gray-700 p-4 rounded-lg">
                <p className="text-gray-400 text-sm">Navigation</p>
                <p className="text-white font-medium">
                  {property?.diagnostics?.validation?.navigation_available ? 'Available' : 'N/A'}
                </p>
              </div>
              <div className="bg-gray-700 p-4 rounded-lg">
                <p className="text-gray-400 text-sm">Geometry</p>
                <p className="text-white font-medium">
                  {property?.diagnostics?.validation?.geometry_valid ? 'Valid' : 'Invalid'}
                </p>
              </div>
            </div>

            <p className="text-gray-300 mb-8">{property?.description}</p>
            
            <button
              onClick={() => setShowContact(!showContact)}
              className="px-6 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition-colors"
            >
              Contact Agent
            </button>

            {showContact && (
              <form onSubmit={handleContactSubmit} className="mt-6 space-y-4 bg-gray-700 p-6 rounded-lg max-w-md">
                <div>
                  <label htmlFor="name" className="block text-sm font-medium text-gray-300 mb-1">
                    Name *
                  </label>
                  <input
                    type="text"
                    id="name"
                    required
                    className="w-full px-3 py-2 bg-gray-600 border border-gray-500 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="Your name"
                  />
                </div>
                <div>
                  <label htmlFor="email" className="block text-sm font-medium text-gray-300 mb-1">
                    Email *
                  </label>
                  <input
                    type="email"
                    id="email"
                    required
                    className="w-full px-3 py-2 bg-gray-600 border border-gray-500 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="your@email.com"
                  />
                </div>
                <div>
                  <label htmlFor="phone" className="block text-sm font-medium text-gray-300 mb-1">
                    Phone
                  </label>
                  <input
                    type="tel"
                    id="phone"
                    className="w-full px-3 py-2 bg-gray-600 border border-gray-500 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="(555) 123-4567"
                  />
                </div>
                <div>
                  <label htmlFor="message" className="block text-sm font-medium text-gray-300 mb-1">
                    Message
                  </label>
                  <textarea
                    id="message"
                    rows={3}
                    className="w-full px-3 py-2 bg-gray-600 border border-gray-500 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="I'm interested in this property..."
                  />
                </div>
                <button
                  type="submit"
                  className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition-colors"
                >
                  Send Inquiry
                </button>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function TourViewerPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-gray-900 flex items-center justify-center"><div className="text-white">Loading...</div></div>}>
      <TourViewerContent />
    </Suspense>
  )
}
