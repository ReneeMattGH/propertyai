'use client'

import { useState, useEffect, Suspense } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase/client'
import Navigation from '@/components/Navigation'

function PropertyUploadContent() {
  const params = useParams()
  const router = useRouter()
  const [file, setFile] = useState<File | null>(null)
  const [mediaType, setMediaType] = useState<'video' | 'image_360' | 'image_standard'>('video')
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [mounted, setMounted] = useState(false)
  const supabase = mounted ? createClient() : null

  useEffect(() => {
    setMounted(true)
  }, [])

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0])
      setError(null)
    }
  }

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (!file) {
      setError('Please select a file to upload')
      return
    }

    if (!supabase) {
      setError('Supabase client not initialized')
      return
    }

    setUploading(true)
    setError(null)
    setProgress(0)

    try {
      const { data: { user } } = await supabase.auth.getUser()
      if (!user) {
        throw new Error('You must be signed in to upload')
      }

      const formData = new FormData()
      formData.append('file', file)
      formData.append('property_id', params.id as string)
      formData.append('media_type', mediaType)
      formData.append('owner_id', user.id)

      // Simulate upload progress
      const progressInterval = setInterval(() => {
        setProgress((prev) => {
          if (prev >= 90) {
            clearInterval(progressInterval)
            return 90
          }
          return prev + 10
        })
      }, 200)

      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/upload/property-media`, {
        method: 'POST',
        body: formData,
      })

      clearInterval(progressInterval)
      setProgress(100)

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Upload failed')
      }

      const result = await response.json()
      setSuccess(true)
      
      setTimeout(() => {
        router.push(`/properties/${params.id}`)
      }, 2000)
    } catch (err: any) {
      setError(err.message || 'Upload failed')
      setUploading(false)
      setProgress(0)
    }
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <Navigation />
      
      <main className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="mb-8">
          <button
            onClick={() => router.back()}
            className="text-gray-400 hover:text-white mb-4 inline-block"
          >
            ← Back to Property
          </button>
          <h1 className="text-3xl font-bold text-white">Upload Property Media</h1>
          <p className="text-gray-400 mt-1">Upload videos or images to create a 3D tour</p>
        </div>

        {success && (
          <div className="bg-green-900/50 border border-green-500 text-green-200 px-4 py-3 rounded mb-6">
            Upload successful! Redirecting...
          </div>
        )}

        {error && (
          <div className="bg-red-900/50 border border-red-500 text-red-200 px-4 py-3 rounded mb-6">
            {error}
          </div>
        )}

        <form onSubmit={handleUpload} className="space-y-6">
          <div className="bg-gray-800 p-6 rounded-lg space-y-4">
            <div>
              <label htmlFor="media_type" className="block text-sm font-medium text-gray-300 mb-1">
                Media Type *
              </label>
              <select
                id="media_type"
                value={mediaType}
                onChange={(e) => setMediaType(e.target.value as any)}
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="video">Video (MP4, MOV, AVI)</option>
                <option value="image_360">360° Image (JPEG, PNG)</option>
                <option value="image_standard">Standard Image (JPEG, PNG)</option>
              </select>
            </div>

            <div>
              <label htmlFor="file" className="block text-sm font-medium text-gray-300 mb-1">
                File *
              </label>
              <input
                type="file"
                id="file"
                onChange={handleFileChange}
                accept={
                  mediaType === 'video'
                    ? 'video/mp4,video/quicktime,video/x-msvideo'
                    : 'image/jpeg,image/png'
                }
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-gray-400 text-sm mt-1">
                Maximum file size: 500MB
              </p>
            </div>

            {file && (
              <div className="bg-gray-700 p-4 rounded">
                <p className="text-white font-medium">{file.name}</p>
                <p className="text-gray-400 text-sm">
                  {(file.size / (1024 * 1024)).toFixed(2)} MB
                </p>
              </div>
            )}

            {uploading && (
              <div>
                <div className="flex justify-between text-sm text-gray-300 mb-1">
                  <span>Uploading...</span>
                  <span>{progress}%</span>
                </div>
                <div className="w-full bg-gray-700 rounded-full h-2">
                  <div
                    className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                    style={{ width: `${progress}%` }}
                  />
                </div>
              </div>
            )}
          </div>

          <div className="flex justify-end space-x-4">
            <button
              type="button"
              onClick={() => router.back()}
              disabled={uploading}
              className="px-4 py-2 bg-gray-700 text-white rounded-lg font-medium hover:bg-gray-600 transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={uploading || !file}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {uploading ? 'Uploading...' : 'Upload'}
            </button>
          </div>
        </form>

        <div className="mt-8 bg-gray-800 p-6 rounded-lg">
          <h3 className="text-lg font-semibold text-white mb-2">Upload Guidelines</h3>
          <ul className="text-gray-400 text-sm space-y-2">
            <li>• Use stable, slow-moving footage for best reconstruction results</li>
            <li>• Ensure good lighting throughout the property</li>
            <li>• Cover all rooms and important features</li>
            <li>• Maximum file size: 500MB</li>
            <li>• Supported video formats: MP4, MOV, AVI</li>
            <li>• Supported image formats: JPEG, PNG</li>
          </ul>
        </div>
      </main>
    </div>
  )
}

export default function PropertyUploadPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-gray-900 flex items-center justify-center"><div className="text-white">Loading...</div></div>}>
      <PropertyUploadContent />
    </Suspense>
  )
}
