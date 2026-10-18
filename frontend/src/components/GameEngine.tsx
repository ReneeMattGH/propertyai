'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader'

interface GameEngineProps {
  jobId: string
}

type GameMode = 'first-person' | 'third-person'
type GameState = 'loading' | 'playing' | 'paused' | 'error'

export default function GameEngine({ jobId }: GameEngineProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const clockRef = useRef<THREE.Clock>(new THREE.Clock())
  
  // Player state
  const playerRef = useRef({
    position: new THREE.Vector3(5, 1.7, 4), // Spawn inside the house
    velocity: new THREE.Vector3(),
    rotation: new THREE.Euler(0, 0, 0, 'YXZ'),
    isGrounded: true,
    speed: 5.0,
    sprintMultiplier: 1.8
  })
  
  // Input state
  const keysRef = useRef<Set<string>>(new Set())
  const mouseRef = useRef({ x: 0, y: 0, locked: false })
  const touchRef = useRef({
    joystickActive: false,
    joystickCenter: { x: 0, y: 0 },
    joystickDelta: { x: 0, y: 0 },
    lookActive: false,
    lookDelta: { x: 0, y: 0 }
  })
  
  // Game state
  const [gameMode, setGameMode] = useState<GameMode>('first-person')
  const [gameState, setGameState] = useState<GameState>('loading')
  const [error, setError] = useState<string | null>(null)
  const [ceilingVisible, setCeilingVisible] = useState(true)
  const [showMinimap, setShowMinimap] = useState(true)
  
  // Avatar (third-person)
  const avatarRef = useRef<THREE.Group | null>(null)
  
  // Scene geometry for collision
  const floorsRef = useRef<THREE.Mesh[]>([])
  const wallsRef = useRef<THREE.Mesh[]>([])
  
  // Initialize Three.js scene
  useEffect(() => {
    if (!containerRef.current) return
    
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x87CEEB)
    sceneRef.current = scene
    
    const camera = new THREE.PerspectiveCamera(
      75,
      containerRef.current.clientWidth / containerRef.current.clientHeight,
      0.1,
      1000
    )
    camera.position.copy(playerRef.current.position)
    cameraRef.current = camera
    
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight)
    renderer.shadowMap.enabled = true
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    containerRef.current.appendChild(renderer.domElement)
    rendererRef.current = renderer
    
    // Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6)
    scene.add(ambientLight)
    
    const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8)
    directionalLight.position.set(10, 20, 10)
    directionalLight.castShadow = true
    scene.add(directionalLight)
    
    // Load scene
    loadScene(scene)
    
    // Handle resize
    const handleResize = () => {
      if (!containerRef.current || !camera || !renderer) return
      camera.aspect = containerRef.current.clientWidth / containerRef.current.clientHeight
      camera.updateProjectionMatrix()
      renderer.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight)
    }
    window.addEventListener('resize', handleResize)
    
    return () => {
      window.removeEventListener('resize', handleResize)
      if (renderer) {
        renderer.dispose()
        containerRef.current?.removeChild(renderer.domElement)
      }
    }
  }, [])
  
  // Load scene and extract collision geometry
  const loadScene = async (scene: THREE.Scene) => {
    try {
      setGameState('loading')
      setError(null)
      
      console.log(`[GameEngine] Loading scene for job ${jobId}`)
      
      const response = await fetch(`http://localhost:8002/reconstructions/${jobId}/assets/scene.glb`)
      console.log(`[GameEngine] Fetch response: ${response.status} ${response.statusText}`)
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }
      
      const glbData = await response.arrayBuffer()
      console.log(`[GameEngine] GLB data size: ${glbData.byteLength} bytes`)
      
      const loader = new GLTFLoader()
      
      const gltf = await new Promise((resolve, reject) => {
        loader.parse(glbData, '', resolve, reject)
      })
      
      console.log(`[GameEngine] GLTF parsed successfully`)
      console.log(`[GameEngine] Scene children: ${gltf.scene.children.length}`)
      
      scene.add(gltf.scene)
      
      // Extract collision geometry from the loaded scene
      extractCollisionGeometry(gltf.scene)
      
      // Create avatar for third-person mode
      createAvatar(scene)
      
      // Set spawn point based on scene bounds
      setSpawnPoint(gltf.scene)
      
      console.log(`[GameEngine] Scene loaded successfully, transitioning to playing state`)
      setGameState('playing')
    } catch (err) {
      console.error('[GameEngine] Error loading scene:', err)
      setError(`Failed to load scene: ${err instanceof Error ? err.message : 'Unknown error'}`)
      setGameState('error')
    }
  }
  
  // Extract floors and walls for collision detection
  const extractCollisionGeometry = (scene: THREE.Object3D) => {
    floorsRef.current = []
    wallsRef.current = []
    
    scene.traverse((child) => {
      if (child instanceof THREE.Mesh) {
        // Simple heuristic: horizontal quads are floors, vertical are walls
        const geometry = child.geometry
        if (geometry) {
          const box = new THREE.Box3().setFromObject(child)
          const size = new THREE.Vector3()
          box.getSize(size)
          
          // If it's wider and deeper than tall, it's likely a floor
          if (size.y < 0.5 && size.x > 1 && size.z > 1) {
            floorsRef.current.push(child)
          } else if (size.y > 1.5) {
            wallsRef.current.push(child)
          }
        }
      }
    })
    
    console.log(`Extracted ${floorsRef.current.length} floors and ${wallsRef.current.length} walls for collision`)
  }
  
  // Create simple avatar for third-person mode
  const createAvatar = (scene: THREE.Scene) => {
    const avatar = new THREE.Group()
    
    // Body
    const bodyGeometry = new THREE.CapsuleGeometry(0.3, 1.2, 4, 8)
    const bodyMaterial = new THREE.MeshStandardMaterial({ color: 0x4a90e2 })
    const body = new THREE.Mesh(bodyGeometry, bodyMaterial)
    body.position.y = 0.9
    avatar.add(body)
    
    // Head
    const headGeometry = new THREE.SphereGeometry(0.2, 16, 16)
    const headMaterial = new THREE.MeshStandardMaterial({ color: 0xffdbac })
    const head = new THREE.Mesh(headGeometry, headMaterial)
    head.position.y = 1.8
    avatar.add(head)
    
    avatar.position.copy(playerRef.current.position)
    avatar.visible = false // Hidden in first-person mode
    scene.add(avatar)
    avatarRef.current = avatar
  }
  
  // Set spawn point based on scene bounds
  const setSpawnPoint = (scene: THREE.Object3D) => {
    const box = new THREE.Box3().setFromObject(scene)
    const center = box.getCenter(new THREE.Vector3())
    
    // Spawn in the center of the scene, slightly above ground
    playerRef.current.position.set(center.x, 1.7, center.z)
    if (cameraRef.current) {
      cameraRef.current.position.copy(playerRef.current.position)
    }
  }
  
  // Game loop
  useEffect(() => {
    if (gameState !== 'playing') return
    
    const animate = () => {
      requestAnimationFrame(animate)
      
      const delta = clockRef.current.getDelta()
      
      if (gameState === 'playing') {
        updatePlayer(delta)
        updateCamera()
      }
      
      if (rendererRef.current && sceneRef.current && cameraRef.current) {
        rendererRef.current.render(sceneRef.current, cameraRef.current)
      }
    }
    
    animate()
    
    return () => {
      // Cleanup
    }
  }, [gameState, gameMode])
  
  // Update player physics and movement
  const updatePlayer = (delta: number) => {
    const player = playerRef.current
    const camera = cameraRef.current
    if (!camera) return
    
    // Calculate movement direction based on camera rotation
    const forward = new THREE.Vector3()
    camera.getWorldDirection(forward)
    forward.y = 0
    forward.normalize()
    
    const right = new THREE.Vector3()
    right.crossVectors(forward, new THREE.Vector3(0, 1, 0))
    
    // Handle keyboard input
    const moveSpeed = player.speed * player.sprintMultiplier * delta
    const isSprinting = keysRef.current.has('Shift')
    player.sprintMultiplier = isSprinting ? 1.8 : 1.0
    
    let moveDirection = new THREE.Vector3()
    
    if (keysRef.current.has('w') || keysRef.current.has('W')) {
      moveDirection.add(forward)
    }
    if (keysRef.current.has('s') || keysRef.current.has('S')) {
      moveDirection.sub(forward)
    }
    if (keysRef.current.has('a') || keysRef.current.has('A')) {
      moveDirection.sub(right)
    }
    if (keysRef.current.has('d') || keysRef.current.has('D')) {
      moveDirection.add(right)
    }
    
    // Handle touch joystick input
    if (touchRef.current.joystickActive) {
      const { x, y } = touchRef.current.joystickDelta
      moveDirection.add(forward.clone().multiplyScalar(-y))
      moveDirection.add(right.clone().multiplyScalar(x))
    }
    
    if (moveDirection.length() > 0) {
      moveDirection.normalize()
      player.position.add(moveDirection.multiplyScalar(moveSpeed))
    }
    
    // Simple collision detection with floors
    let onFloor = false
    for (const floor of floorsRef.current) {
      const box = new THREE.Box3().setFromObject(floor)
      if (box.containsPoint(player.position)) {
        onFloor = true
        break
      }
    }
    
    // Simple gravity
    if (!onFloor && player.position.y > 0) {
      player.position.y -= 9.8 * delta
    } else if (player.position.y < 1.7) {
      player.position.y = 1.7
    }
    
    // Simple wall collision (prevent going outside scene bounds)
    // In a full implementation, this would use proper physics
    const playerBox = new THREE.Box3(
      new THREE.Vector3(player.position.x - 0.3, player.position.y - 1.7, player.position.z - 0.3),
      new THREE.Vector3(player.position.x + 0.3, player.position.y + 0.3, player.position.z + 0.3)
    )
    
    for (const wall of wallsRef.current) {
      const wallBox = new THREE.Box3().setFromObject(wall)
      if (playerBox.intersectsBox(wallBox)) {
        // Simple collision response: push back
        const wallCenter = new THREE.Vector3()
        wallBox.getCenter(wallCenter)
        const pushDirection = player.position.clone().sub(wallCenter).normalize()
        player.position.add(pushDirection.multiplyScalar(0.1))
      }
    }
    
    // Update avatar position
    if (avatarRef.current) {
      avatarRef.current.position.copy(player.position)
      avatarRef.current.visible = gameMode === 'third-person'
    }
  }
  
  // Update camera based on mode
  const updateCamera = () => {
    const camera = cameraRef.current
    const player = playerRef.current
    if (!camera) return
    
    if (gameMode === 'first-person') {
      // Camera at player position with rotation
      camera.position.copy(player.position)
      camera.rotation.copy(player.rotation)
    } else {
      // Third-person: camera behind and above player
      const offset = new THREE.Vector3(0, 2, 5)
      offset.applyEuler(player.rotation)
      camera.position.copy(player.position).add(offset)
      camera.lookAt(player.position)
    }
  }
  
  // Keyboard input handlers
  const handleKeyDown = (e: KeyboardEvent) => {
    keysRef.current.add(e.key)
    
    if (e.key === 'Escape') {
      setGameState(gameState === 'paused' ? 'playing' : 'paused')
    }
    
    if (e.key === 'c' || e.key === 'C') {
      setGameMode(gameMode === 'first-person' ? 'third-person' : 'first-person')
    }
    
    if (e.key === 'r' || e.key === 'R') {
      resetPosition()
    }
  }
  
  const handleKeyUp = (e: KeyboardEvent) => {
    keysRef.current.delete(e.key)
  }
  
  // Mouse look handler
  const handleMouseMove = (e: MouseEvent) => {
    if (mouseRef.current.locked && gameState === 'playing') {
      const sensitivity = 0.002
      playerRef.current.rotation.y -= e.movementX * sensitivity
      playerRef.current.rotation.x -= e.movementY * sensitivity
      
      // Clamp vertical look
      playerRef.current.rotation.x = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, playerRef.current.rotation.x))
    }
  }
  
  const handlePointerLock = () => {
    if (containerRef.current && gameState === 'playing') {
      containerRef.current.requestPointerLock()
    }
  }
  
  // Touch handlers for mobile
  const handleTouchStart = (e: React.TouchEvent) => {
    e.preventDefault()
    
    for (const touch of e.touches) {
      const rect = containerRef.current?.getBoundingClientRect()
      if (!rect) continue
      
      const x = touch.clientX - rect.left
      const y = touch.clientY - rect.top
      
      // Left side of screen: joystick
      if (x < rect.width / 2) {
        touchRef.current.joystickActive = true
        touchRef.current.joystickCenter = { x, y }
        touchRef.current.joystickDelta = { x: 0, y: 0 }
      }
      // Right side of screen: look
      else {
        touchRef.current.lookActive = true
        touchRef.current.lookDelta = { x: 0, y: 0 }
      }
    }
  }
  
  const handleTouchMove = (e: React.TouchEvent) => {
    e.preventDefault()
    
    for (const touch of e.touches) {
      const rect = containerRef.current?.getBoundingClientRect()
      if (!rect) continue
      
      const x = touch.clientX - rect.left
      const y = touch.clientY - rect.top
      
      if (x < rect.width / 2 && touchRef.current.joystickActive) {
        // Update joystick
        const dx = x - touchRef.current.joystickCenter.x
        const dy = y - touchRef.current.joystickCenter.y
        const maxDistance = 50
        const distance = Math.sqrt(dx * dx + dy * dy)
        const clampedDistance = Math.min(distance, maxDistance)
        const normalizedDistance = clampedDistance / maxDistance
        
        touchRef.current.joystickDelta = {
          x: (dx / distance) * normalizedDistance,
          y: (dy / distance) * normalizedDistance
        }
      } else if (x >= rect.width / 2 && touchRef.current.lookActive) {
        // Update look
        const sensitivity = 0.005
        touchRef.current.lookDelta = { x, y }
        
        playerRef.current.rotation.y -= x * sensitivity
        playerRef.current.rotation.x -= y * sensitivity
        playerRef.current.rotation.x = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, playerRef.current.rotation.x))
      }
    }
  }
  
  const handleTouchEnd = (e: React.TouchEvent) => {
    e.preventDefault()
    
    if (e.touches.length === 0) {
      touchRef.current.joystickActive = false
      touchRef.current.lookActive = false
      touchRef.current.joystickDelta = { x: 0, y: 0 }
    }
  }
  
  // Reset position to spawn point
  const resetPosition = useCallback(() => {
    if (sceneRef.current) {
      const box = new THREE.Box3().setFromObject(sceneRef.current)
      const center = box.getCenter(new THREE.Vector3())
      playerRef.current.position.set(center.x, 1.7, center.z)
    }
  }, [])
  
  // Register input handlers
  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)
    window.addEventListener('mousemove', handleMouseMove)
    
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
      window.removeEventListener('mousemove', handleMouseMove)
    }
  }, [gameState, gameMode])
  
  // Render loading state
  if (gameState === 'loading') {
    return (
      <div className="w-full h-full flex items-center justify-center bg-gray-900">
        <div className="text-white text-center">
          <div className="text-2xl mb-2">Loading House...</div>
          <div className="text-gray-400">Preparing 3D environment</div>
        </div>
      </div>
    )
  }
  
  // Render error state
  if (gameState === 'error') {
    return (
      <div className="w-full h-full flex items-center justify-center bg-gray-900">
        <div className="text-center text-white">
          <div className="text-red-400 text-xl mb-2">Failed to Load House</div>
          <div className="text-gray-400 mb-4">{error}</div>
          <button
            onClick={() => window.location.reload()}
            className="px-4 py-2 bg-blue-600 rounded hover:bg-blue-700"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }
  
  // Render paused state
  if (gameState === 'paused') {
    return (
      <div className="w-full h-full relative">
        <div ref={containerRef} className="w-full h-full" />
        <div className="absolute inset-0 bg-black/70 flex items-center justify-center">
          <div className="text-white text-center">
            <div className="text-3xl mb-4">PAUSED</div>
            <button
              onClick={() => setGameState('playing')}
              className="px-6 py-3 bg-blue-600 rounded-lg text-xl hover:bg-blue-700"
            >
              Resume
            </button>
          </div>
        </div>
      </div>
    )
  }
  
  // Render game
  return (
    <div className="relative w-full h-full">
      <div
        ref={containerRef}
        className="w-full h-full"
        onClick={handlePointerLock}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
      />
      
      {/* Game HUD */}
      <div className="absolute top-4 left-4 bg-black/50 p-4 rounded-lg text-white text-sm">
        <div className="font-semibold mb-2">Controls</div>
        <div>Mode: {gameMode === 'first-person' ? 'First Person' : 'Third Person'}</div>
        {gameMode === 'first-person' && (
          <div className="text-gray-300 mt-1">
            <div>WASD - Move</div>
            <div>Mouse - Look</div>
            <div>Shift - Sprint</div>
            <div>C - Switch Mode</div>
            <div>R - Reset Position</div>
            <div>ESC - Pause</div>
          </div>
        )}
        {gameMode === 'third-person' && (
          <div className="text-gray-300 mt-1">
            <div>WASD - Move</div>
            <div>Mouse - Rotate Camera</div>
            <div>C - Switch Mode</div>
            <div>R - Reset Position</div>
            <div>ESC - Pause</div>
          </div>
        )}
      </div>
      
      {/* Mobile controls hint */}
      <div className="absolute bottom-4 left-4 bg-black/50 px-3 py-2 rounded text-white text-sm md:hidden">
        <div className="text-gray-300">
          <div>Left side: Move</div>
          <div>Right side: Look</div>
        </div>
      </div>
      
      {/* Toggle ceiling */}
      <div className="absolute top-4 right-4 bg-black/50 p-2 rounded-lg">
        <button
          onClick={() => setCeilingVisible(!ceilingVisible)}
          className="text-white text-sm px-3 py-1 bg-blue-600 rounded hover:bg-blue-700"
        >
          {ceilingVisible ? 'Hide Ceiling' : 'Show Ceiling'}
        </button>
      </div>
      
      {/* Position indicator */}
      <div className="absolute bottom-4 right-4 bg-black/50 px-3 py-2 rounded text-white text-sm">
        <div>
          Position: {playerRef.current.position.x.toFixed(1)}, {playerRef.current.position.y.toFixed(1)}, {playerRef.current.position.z.toFixed(1)}
        </div>
      </div>
    </div>
  )
}
