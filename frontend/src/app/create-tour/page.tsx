'use client'

import { useState } from 'react'
import Navigation from '@/components/Navigation'

export default function CreateTourPage() {
  const [projectName, setProjectName] = useState('')
  const [floorPlan, setFloorPlan] = useState<File | null>(null)
  const [photos, setPhotos] = useState<File[]>([])
  const [numFloors, setNumFloors] = useState(1)
  const [knownWidth, setKnownWidth] = useState('')
  const [knownLength, setKnownLength] = useState('')
  const [processing, setProcessing] = useState(false)
  const [status, setStatus] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)

  const handleFloorPlanUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0]
      const validTypes = ['image/png', 'image/jpeg', 'image/jpg', 'application/pdf']
      if (!validTypes.includes(file.type)) {
        alert('Please upload a PNG, JPG, JPEG, or PDF file')
        return
      }
      if (file.size > 10 * 1024 * 1024) {
        alert('File too large. Maximum 10MB')
        return
      }
      setFloorPlan(file)
    }
  }

  const handlePhotoUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const newPhotos = Array.from(e.target.files).filter(file => {
        const validTypes = ['image/png', 'image/jpeg', 'image/jpg']
        return validTypes.includes(file.type) && file.size <= 10 * 1024 * 1024
      })
      setPhotos(prev => [...prev, ...newPhotos])
    }
  }

  const removePhoto = (index: number) => {
    setPhotos(prev => prev.filter((_, i) => i !== index))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (!projectName) {
      alert('Please enter a project name')
      return
    }
    
    if (!floorPlan) {
      alert('Please upload a floor plan')
      return
    }
    
    if (photos.length === 0) {
      alert('Please upload at least one photo')
      return
    }

    setProcessing(true)
    setStatus('Initializing reconstruction...')

    try {
      // Create form data
      const formData = new FormData()
      formData.append('project_name', projectName)
      formData.append('floor_plan', floorPlan)
      formData.append('num_floors', numFloors.toString())
      if (knownWidth) formData.append('known_width', knownWidth)
      if (knownLength) formData.append('known_length', knownLength)
      
      // Append all photos with the same field name 'photos'
      photos.forEach((photo) => {
        formData.append('photos', photo)
      })

      // Submit to reconstruction engine
      const response = await fetch('http://localhost:8002/reconstructions', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }))
        throw new Error(errorData.detail || `Failed to start reconstruction: ${response.status}`)
      }

      const data = await response.json()
      setJobId(data.job_id)
      setStatus('Reconstruction started. Processing...')
      
      // Poll for status
      pollStatus(data.job_id)
    } catch (error) {
      console.error('Error:', error)
      const errorMessage = error instanceof Error ? error.message : 'Unknown error'
      setStatus(`Error: ${errorMessage}`)
      setProcessing(false)
    }
  }

  const pollStatus = async (id: string) => {
    const interval = setInterval(async () => {
      try {
        const response = await fetch(`http://localhost:8002/reconstructions/${id}`)
        
        if (!response.ok) {
          clearInterval(interval)
          setProcessing(false)
          setStatus(`Error checking status: ${response.status}`)
          return
        }
        
        const data = await response.json()
        
        // Display detailed status
        const stageNames = {
          'created': 'Initializing',
          'validating': 'Validating inputs',
          'parsing_floor_plan': 'Parsing floor plan',
          'analyzing_photos': 'Analyzing photographs',
          'building_geometry': 'Building 3D geometry',
          'reconstructing_appearance': 'Reconstructing appearance',
          'validating_scene': 'Validating scene',
          'ready_for_review': 'Ready for review',
          'completed': 'Completed',
          'failed': 'Failed'
        }
        
        const stageName = stageNames[data.current_stage] || data.current_stage
        setStatus(`Status: ${stageName} (${data.progress.toFixed(0)}%)`)
        
        if (data.status === 'completed') {
          clearInterval(interval)
          setProcessing(false)
          setStatus('✓ Reconstruction completed! View the tour below.')
        } else if (data.status === 'failed') {
          clearInterval(interval)
          setProcessing(false)
          const errorMsg = data.error ? data.error.split('\n')[0] : 'Unknown error'
          setStatus(`✗ Reconstruction failed: ${errorMsg}`)
        }
      } catch (error) {
        console.error('Error polling status:', error)
        clearInterval(interval)
        setProcessing(false)
        setStatus('Error checking status - please check if backend is running')
      }
    }, 2000)
  }

  const handleViewTour = () => {
    if (jobId) {
      window.location.href = `/tour/${jobId}`
    }
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <Navigation />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white">Create Property Tour</h1>
          <p className="text-gray-400 mt-1">
            Upload a floor plan and photographs to generate an interactive 3D walkthrough
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Upload Form */}
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-xl font-semibold text-white mb-6">Property Information</h2>
            
            <form onSubmit={handleSubmit} className="space-y-6">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Project Name *
                </label>
                <input
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="e.g., 123 Main Street"
                  required
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Floor Plan * (PNG, JPG, JPEG, or PDF)
                </label>
                <input
                  type="file"
                  accept=".png,.jpg,.jpeg,.pdf"
                  onChange={handleFloorPlanUpload}
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  required
                />
                {floorPlan && (
                  <p className="text-sm text-green-400 mt-2">
                    ✓ {floorPlan.name} ({(floorPlan.size / 1024 / 1024).toFixed(2)} MB)
                  </p>
                )}
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    Number of Floors
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="5"
                    value={numFloors}
                    onChange={(e) => setNumFloors(parseInt(e.target.value) || 1)}
                    className="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    Known Width (meters, optional)
                  </label>
                  <input
                    type="number"
                    step="0.1"
                    value={knownWidth}
                    onChange={(e) => setKnownWidth(e.target.value)}
                    className="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="e.g., 10.5"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Known Length (meters, optional)
                </label>
                <input
                  type="number"
                  step="0.1"
                  value={knownLength}
                  onChange={(e) => setKnownLength(e.target.value)}
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="e.g., 15.0"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  House Photographs * (PNG, JPG, JPEG)
                </label>
                <input
                  type="file"
                  accept=".png,.jpg,.jpeg"
                  multiple
                  onChange={handlePhotoUpload}
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  required
                />
                <p className="text-sm text-gray-400 mt-2">
                  Upload photos of each room (living room, bedrooms, kitchen, etc.)
                </p>
              </div>

              {photos.length > 0 && (
                <div>
                  <p className="text-sm font-medium text-gray-300 mb-2">
                    Uploaded Photos ({photos.length})
                  </p>
                  <div className="space-y-2 max-h-48 overflow-y-auto">
                    {photos.map((photo, index) => (
                      <div
                        key={index}
                        className="flex items-center justify-between bg-gray-700 rounded px-3 py-2"
                      >
                        <span className="text-sm text-white truncate flex-1">
                          {photo.name}
                        </span>
                        <button
                          type="button"
                          onClick={() => removePhoto(index)}
                          className="ml-2 text-red-400 hover:text-red-300 text-sm"
                        >
                          Remove
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <button
                type="submit"
                disabled={processing}
                className="w-full bg-blue-600 text-white py-3 rounded-lg font-medium hover:bg-blue-700 transition-colors disabled:bg-gray-600 disabled:cursor-not-allowed"
              >
                {processing ? 'Processing...' : 'Start Reconstruction'}
              </button>
            </form>
          </div>

          {/* Status Panel */}
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-xl font-semibold text-white mb-6">Reconstruction Status</h2>
            
            {status && (
              <div className="bg-gray-700 rounded-lg p-4 mb-6">
                <p className="text-white">{status}</p>
              </div>
            )}

            <div className="space-y-4">
              <div className="flex items-start">
                <div className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center mr-3">
                  <span className="text-white text-xs font-bold">1</span>
                </div>
                <div>
                  <h3 className="text-white font-medium">Validate Inputs</h3>
                  <p className="text-gray-400 text-sm">Check file formats, sizes, and completeness</p>
                </div>
              </div>

              <div className="flex items-start">
                <div className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center mr-3">
                  <span className="text-white text-xs font-bold">2</span>
                </div>
                <div>
                  <h3 className="text-white font-medium">Parse Floor Plan</h3>
                  <p className="text-gray-400 text-sm">Extract walls, rooms, and doorways from plan</p>
                </div>
              </div>

              <div className="flex items-start">
                <div className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center mr-3">
                  <span className="text-white text-xs font-bold">3</span>
                </div>
                <div>
                  <h3 className="text-white font-medium">Analyze Photographs</h3>
                  <p className="text-gray-400 text-sm">Assign photos to rooms and extract visual features</p>
                </div>
              </div>

              <div className="flex items-start">
                <div className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center mr-3">
                  <span className="text-white text-xs font-bold">4</span>
                </div>
                <div>
                  <h3 className="text-white font-medium">Build 3D Geometry</h3>
                  <p className="text-gray-400 text-sm">Generate floors, walls, and collision geometry</p>
                </div>
              </div>

              <div className="flex items-start">
                <div className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center mr-3">
                  <span className="text-white text-xs font-bold">5</span>
                </div>
                <div>
                  <h3 className="text-white font-medium">Reconstruct Appearance</h3>
                  <p className="text-gray-400 text-sm">Apply textures and visual details from photos</p>
                </div>
              </div>

              <div className="flex items-start">
                <div className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center mr-3">
                  <span className="text-white text-xs font-bold">6</span>
                </div>
                <div>
                  <h3 className="text-white font-medium">Validate & Export</h3>
                  <p className="text-gray-400 text-sm">Check navigation and generate final scene</p>
                </div>
              </div>
            </div>

            {jobId && !processing && status.includes('completed') && (
              <button
                onClick={handleViewTour}
                className="w-full mt-6 bg-green-600 text-white py-3 rounded-lg font-medium hover:bg-green-700 transition-colors"
              >
                View 3D Walkthrough
              </button>
            )}
          </div>
        </div>

        {/* Info Box */}
        <div className="mt-8 bg-blue-900/30 border border-blue-500 rounded-lg p-6">
          <h3 className="text-white font-semibold mb-2">How It Works</h3>
          <ul className="text-gray-300 text-sm space-y-2">
            <li>• Upload a floor plan to establish the architectural layout</li>
            <li>• Provide photos of each room to capture real appearance</li>
            <li>• Optional: Enter known dimensions to establish scale</li>
            <li>• The AI reconstructs a 3D model you can walk through</li>
            <li>• Explore the property in first-person or third-person mode</li>
          </ul>
        </div>
      </main>
    </div>
  )
}
