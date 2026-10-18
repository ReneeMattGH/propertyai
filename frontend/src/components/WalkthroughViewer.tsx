'use client'

import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls'
import { PointerLockControls } from 'three/examples/jsm/controls/PointerLockControls'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader'

interface WalkthroughViewerProps {
  jobId: string
  sceneUrl?: string
}

export default function WalkthroughViewer({ jobId, sceneUrl }: WalkthroughViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const orbitControlsRef = useRef<OrbitControls | null>(null)
  const pointerControlsRef = useRef<PointerLockControls | null>(null)
  
  const [mode, setMode] = useState<'first-person' | 'third-person'>('first-person')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [position, setPosition] = useState({ x: 0, y: 1.7, z: 0 })

  useEffect(() => {
    if (!containerRef.current) return

    // Initialize scene
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x87CEEB) // Sky blue
    sceneRef.current = scene

    // Initialize camera
    const camera = new THREE.PerspectiveCamera(
      75,
      containerRef.current.clientWidth / containerRef.current.clientHeight,
      0.1,
      1000
    )
    camera.position.set(position.x, position.y, position.z)
    cameraRef.current = camera

    // Initialize renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight)
    renderer.shadowMap.enabled = true
    containerRef.current.appendChild(renderer.domElement)
    rendererRef.current = renderer

    // Add lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6)
    scene.add(ambientLight)

    const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8)
    directionalLight.position.set(10, 20, 10)
    directionalLight.castShadow = true
    scene.add(directionalLight)

    // Initialize controls
    const orbitControls = new OrbitControls(camera, renderer.domElement)
    orbitControls.enableDamping = true
    orbitControls.dampingFactor = 0.05
    orbitControlsRef.current = orbitControls

    const pointerControls = new PointerLockControls(camera, document.body)
    pointerControlsRef.current = pointerControls

    // Load scene
    loadScene(scene, camera)

    // Handle resize
    const handleResize = () => {
      if (!containerRef.current || !camera || !renderer) return
      camera.aspect = containerRef.current.clientWidth / containerRef.current.clientHeight
      camera.updateProjectionMatrix()
      renderer.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight)
    }
    window.addEventListener('resize', handleResize)

    // Animation loop
    const animate = () => {
      requestAnimationFrame(animate)
      
      if (mode === 'third-person' && orbitControls) {
        orbitControls.update()
      }
      
      if (renderer && scene && camera) {
        renderer.render(scene, camera)
      }
    }
    animate()

    return () => {
      window.removeEventListener('resize', handleResize)
      if (renderer) {
        renderer.dispose()
        containerRef.current?.removeChild(renderer.domElement)
      }
    }
  }, [])

  const loadScene = async (scene: THREE.Scene, camera: THREE.PerspectiveCamera) => {
    try {
      setLoading(true)
      setError(null)

      // Try to load GLB first (better format for Three.js)
      let glbLoaded = false
      try {
        const response = await fetch(`http://localhost:8002/reconstructions/${jobId}/assets/scene.glb`)
        if (response.ok) {
          const glbData = await response.arrayBuffer()
          const loader = new GLTFLoader()
          
          // Use parse with proper parameters
          const gltf = await new Promise((resolve, reject) => {
            loader.parse(glbData, '', resolve, reject)
          })
          
          scene.add(gltf.scene)
          
          // Center camera on scene
          const box = new THREE.Box3().setFromObject(gltf.scene)
          const center = box.getCenter(new THREE.Vector3())
          camera.position.set(center.x, 1.7, center.z + 5)
          camera.lookAt(center)
          
          glbLoaded = true
          setLoading(false)
          return
        }
      } catch (e) {
        console.log('GLB loading failed, trying OBJ:', e)
      }

      // Fall back to OBJ if GLB not available
      if (!glbLoaded) {
        const response = await fetch(`http://localhost:8002/reconstructions/${jobId}/assets/scene.obj`)
        
        if (!response.ok) {
          throw new Error('Failed to load scene file')
        }

        const objText = await response.text()
        
        // Parse OBJ file
        const geometry = parseOBJ(objText)
        
        // Create mesh
        const material = new THREE.MeshStandardMaterial({
          color: 0xcccccc,
          side: THREE.DoubleSide
        })
        
        const mesh = new THREE.Mesh(geometry, material)
        mesh.castShadow = true
        mesh.receiveShadow = true
        scene.add(mesh)

        // Center camera on scene
        const box = new THREE.Box3().setFromObject(mesh)
        const center = box.getCenter(new THREE.Vector3())
        camera.position.set(center.x, 1.7, center.z + 5)
        camera.lookAt(center)

        setLoading(false)
      }
    } catch (err) {
      console.error('Error loading scene:', err)
      setError(`Failed to load 3D scene: ${err instanceof Error ? err.message : 'Unknown error'}`)
      setLoading(false)
      
      // Load fallback sample scene
      loadSampleScene(scene, camera)
    }
  }

  const parseOBJ = (objText: string): THREE.BufferGeometry => {
    const vertices: number[] = []
    const faces: number[] = []
    
    const lines = objText.split('\n')
    const vertexPositions: THREE.Vector3[] = []
    
    for (const line of lines) {
      const parts = line.trim().split(/\s+/)
      if (parts[0] === 'v') {
        const x = parseFloat(parts[1])
        const y = parseFloat(parts[2])
        const z = parseFloat(parts[3])
        vertexPositions.push(new THREE.Vector3(x, y, z))
      } else if (parts[0] === 'f') {
        // OBJ faces are 1-indexed
        const v1 = parseInt(parts[1]) - 1
        const v2 = parseInt(parts[2]) - 1
        const v3 = parseInt(parts[3]) - 1
        faces.push(v1, v2, v3)
      }
    }
    
    const geometry = new THREE.BufferGeometry()
    const positions: number[] = []
    
    for (const vertex of vertexPositions) {
      positions.push(vertex.x, vertex.y, vertex.z)
    }
    
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
    geometry.setIndex(faces)
    geometry.computeVertexNormals()
    
    return geometry
  }

  const loadSampleScene = (scene: THREE.Scene, camera: THREE.PerspectiveCamera) => {
    // Create a simple room as fallback
    const roomGeometry = new THREE.BoxGeometry(10, 3, 8)
    const roomMaterial = new THREE.MeshStandardMaterial({ 
      color: 0xcccccc,
      side: THREE.BackSide // Render inside of box
    })
    const room = new THREE.Mesh(roomGeometry, roomMaterial)
    scene.add(room)

    // Add floor
    const floorGeometry = new THREE.PlaneGeometry(10, 8)
    const floorMaterial = new THREE.MeshStandardMaterial({ color: 0x8B4513 })
    const floor = new THREE.Mesh(floorGeometry, floorMaterial)
    floor.rotation.x = -Math.PI / 2
    floor.position.y = 0
    scene.add(floor)

    camera.position.set(0, 1.7, 5)
    camera.lookAt(0, 1.7, 0)
  }

  const toggleMode = () => {
    setMode(prev => prev === 'first-person' ? 'third-person' : 'first-person')
    
    if (pointerControlsRef.current && orbitControlsRef.current) {
      if (mode === 'first-person') {
        pointerControlsRef.current.unlock()
        orbitControlsRef.current.enabled = true
      } else {
        orbitControlsRef.current.enabled = false
        pointerControlsRef.current.lock()
      }
    }
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (mode !== 'first-person' || !cameraRef.current) return
    
    const moveSpeed = 0.1
    const camera = cameraRef.current
    
    switch (e.key.toLowerCase()) {
      case 'w':
        camera.translateZ(-moveSpeed)
        break
      case 's':
        camera.translateZ(moveSpeed)
        break
      case 'a':
        camera.translateX(-moveSpeed)
        break
      case 'd':
        camera.translateX(moveSpeed)
        break
    }
  }

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [mode])

  if (loading) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-gray-900">
        <div className="text-white">Loading 3D scene...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-gray-900">
        <div className="text-center">
          <div className="text-red-400 mb-2">{error}</div>
          <div className="text-gray-400 text-sm">Showing sample scene instead</div>
        </div>
      </div>
    )
  }

  return (
    <div className="relative w-full h-full">
      <div ref={containerRef} className="w-full h-full" />
      
      {/* Controls UI */}
      <div className="absolute top-4 left-4 bg-black/50 p-4 rounded-lg text-white">
        <div className="mb-2 font-semibold">Controls</div>
        <div className="text-sm space-y-1">
          <div>Mode: {mode === 'first-person' ? 'First Person' : 'Third Person'}</div>
          {mode === 'first-person' && (
            <div className="text-gray-300">
              <div>WASD - Move</div>
              <div>Mouse - Look</div>
            </div>
          )}
          {mode === 'third-person' && (
            <div className="text-gray-300">
              <div>Mouse - Rotate</div>
              <div>Scroll - Zoom</div>
            </div>
          )}
        </div>
        <button
          onClick={toggleMode}
          className="mt-3 px-3 py-1 bg-blue-600 rounded text-sm hover:bg-blue-700"
        >
          Switch Mode
        </button>
      </div>

      {/* Position indicator */}
      <div className="absolute bottom-4 left-4 bg-black/50 px-3 py-2 rounded text-white text-sm">
        Position: {position.x.toFixed(1)}, {position.y.toFixed(1)}, {position.z.toFixed(1)}
      </div>
    </div>
  )
}
