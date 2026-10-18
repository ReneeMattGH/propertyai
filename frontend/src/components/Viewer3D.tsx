'use client'

import { Canvas } from '@react-three/fiber'
import { OrbitControls, Environment, Stage, ContactShadows, useGLTF } from '@react-three/drei'
import { Suspense, useState } from 'react'
import * as THREE from 'three'

interface ModelProps {
  url: string
}

function Model({ url }: ModelProps) {
  const { scene } = useGLTF(url)
  return <primitive object={scene} />
}

interface Viewer3DProps {
  modelUrl?: string
  className?: string
}

export default function Viewer3D({ modelUrl, className = '' }: Viewer3DProps) {
  const [viewMode, setViewMode] = useState<'first-person' | 'third-person'>('third-person')
  const [loading, setLoading] = useState(true)

  // Sample model URL - replace with actual property model when available
  const defaultModelUrl = 'https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Models/master/2.0/DamagedHelmet/glTF-Binary/DamagedHelmet.glb'

  const currentModelUrl = modelUrl || defaultModelUrl

  return (
    <div className={`relative w-full h-full ${className}`}>
      <div className="absolute top-4 left-4 z-10 bg-gray-900/80 backdrop-blur-sm p-4 rounded-lg">
        <h3 className="text-white font-semibold mb-2">View Mode</h3>
        <div className="flex space-x-2">
          <button
            onClick={() => setViewMode('first-person')}
            className={`px-3 py-1 rounded text-sm font-medium transition-colors ${
              viewMode === 'first-person'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            First Person
          </button>
          <button
            onClick={() => setViewMode('third-person')}
            className={`px-3 py-1 rounded text-sm font-medium transition-colors ${
              viewMode === 'third-person'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            Third Person
          </button>
        </div>
      </div>

      <div className="absolute top-4 right-4 z-10 bg-gray-900/80 backdrop-blur-sm p-4 rounded-lg">
        <h3 className="text-white font-semibold mb-2">Controls</h3>
        <ul className="text-gray-300 text-sm space-y-1">
          <li>• Left click + drag: Rotate</li>
          <li>• Right click + drag: Pan</li>
          <li>• Scroll: Zoom</li>
        </ul>
      </div>

      <Canvas
        shadows
        camera={{ position: [0, 2, 5], fov: 50 }}
        className="w-full h-full"
      >
        <Suspense fallback={null}>
          <Stage
            environment="city"
            intensity={0.6}
            shadows={{ type: 'accumulative', color: '#1a1a1a', colorBlend: 2, opacity: 1 }}
            adjustCamera={1.5}
          >
            <Model url={currentModelUrl} />
          </Stage>
          <ContactShadows
            position={[0, -0.5, 0]}
            opacity={0.7}
            scale={10}
            blur={2.5}
            far={10}
            resolution={256}
            color="#000000"
          />
          <Environment preset="city" />
          <OrbitControls
            makeDefault
            minPolarAngle={0}
            maxPolarAngle={Math.PI / 2}
            enableZoom={true}
            enablePan={true}
            enableRotate={true}
          />
        </Suspense>
      </Canvas>

      {!modelUrl && (
        <div className="absolute bottom-4 left-4 right-4 z-10 bg-yellow-900/80 backdrop-blur-sm p-4 rounded-lg">
          <p className="text-yellow-200 text-sm">
            <strong>Demo Mode:</strong> Showing sample 3D model. Upload property footage to generate your own 3D tour.
          </p>
        </div>
      )}
    </div>
  )
}
