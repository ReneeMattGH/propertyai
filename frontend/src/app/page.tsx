import Link from 'next/link'
import Navigation from '@/components/Navigation'

export default function Home() {
  return (
    <div className="min-h-screen bg-gray-900">
      <Navigation />
      
      <main>
        {/* Hero Section */}
        <section className="pt-20 pb-32 px-4 sm:px-6 lg:px-8">
          <div className="max-w-7xl mx-auto text-center">
            <h1 className="text-4xl sm:text-5xl md:text-6xl font-bold text-white mb-6">
              Transform Floor Plans Into
              <span className="text-blue-500"> Interactive 3D Walkthroughs</span>
            </h1>
            <p className="text-xl text-gray-400 mb-8 max-w-3xl mx-auto">
              Upload a 2D floor plan. The engine reconstructs a validated, walkable 3D house,
              infers room types, and furnishes every room with real 3D models you can explore
              in your browser.
            </p>
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <Link
                href="/create-tour"
                className="bg-blue-600 text-white px-8 py-3 rounded-lg font-medium hover:bg-blue-700 transition-colors"
              >
                Create a Tour
              </Link>
              <Link
                href="/tours"
                className="bg-gray-800 text-white px-8 py-3 rounded-lg font-medium hover:bg-gray-700 transition-colors border border-gray-700"
              >
                View Tours
              </Link>
            </div>
          </div>
        </section>

        {/* Features Section */}
        <section className="py-20 bg-gray-800">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <h2 className="text-3xl font-bold text-white text-center mb-12">
              How It Works
            </h2>
            <div className="grid md:grid-cols-3 gap-8">
              <div className="bg-gray-900 p-6 rounded-lg">
                <div className="w-12 h-12 bg-blue-600 rounded-lg flex items-center justify-center mb-4">
                  <span className="text-white text-xl font-bold">1</span>
                </div>
                <h3 className="text-xl font-semibold text-white mb-2">Upload a Floor Plan</h3>
                <p className="text-gray-400">
                  A PNG, JPEG, or PDF plan is all you need. Supplying the property width and
                  length makes the reconstructed scale measured rather than estimated.
                </p>
              </div>
              <div className="bg-gray-900 p-6 rounded-lg">
                <div className="w-12 h-12 bg-blue-600 rounded-lg flex items-center justify-center mb-4">
                  <span className="text-white text-xl font-bold">2</span>
                </div>
                <h3 className="text-xl font-semibold text-white mb-2">Furnished 3D Reconstruction</h3>
                <p className="text-gray-400">
                  Walls, rooms, doors, and windows are vectorised and extruded, room types are
                  inferred, and each room is furnished with deterministic 3D models before the
                  scene is exported and validated.
                </p>
              </div>
              <div className="bg-gray-900 p-6 rounded-lg">
                <div className="w-12 h-12 bg-blue-600 rounded-lg flex items-center justify-center mb-4">
                  <span className="text-white text-xl font-bold">3</span>
                </div>
                <h3 className="text-xl font-semibold text-white mb-2">Walk & Explore</h3>
                <p className="text-gray-400">
                  Move through the property with WASD or arrow keys, mouse-look, and pointer lock,
                  or use the on-screen joystick and touch-look controls on mobile. Gravity,
                  collision, and doorways are simulated.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Benefits Section */}
        <section className="py-20">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <h2 className="text-3xl font-bold text-white text-center mb-12">
              Why RealEstAI?
            </h2>
            <div className="grid md:grid-cols-2 gap-8">
              <div className="flex items-start space-x-4">
                <div className="w-8 h-8 bg-green-600 rounded-full flex items-center justify-center flex-shrink-0 mt-1">
                  <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white mb-1">Immersive Experience</h3>
                  <p className="text-gray-400">
                    Game-like navigation with keyboard, mouse, and mobile controls. Buyers feel like they&apos;re actually walking through the property.
                  </p>
                </div>
              </div>
              <div className="flex items-start space-x-4">
                <div className="w-8 h-8 bg-green-600 rounded-full flex items-center justify-center flex-shrink-0 mt-1">
                  <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white mb-1">Measured Scale</h3>
                  <p className="text-gray-400">
                    Supply the property width and length to get a measured reconstruction, or let the
                    engine estimate the scale and warn you when it does.
                  </p>
                </div>
              </div>
              <div className="flex items-start space-x-4">
                <div className="w-8 h-8 bg-green-600 rounded-full flex items-center justify-center flex-shrink-0 mt-1">
                  <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white mb-1">Shareable Links</h3>
                  <p className="text-gray-400">
                    Generate shareable browser links for each tour. No app download required for buyers.
                  </p>
                </div>
              </div>
              <div className="flex items-start space-x-4">
                <div className="w-8 h-8 bg-green-600 rounded-full flex items-center justify-center flex-shrink-0 mt-1">
                  <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white mb-1">Validation Report</h3>
                  <p className="text-gray-400">
                    Every tour exposes its manifest, scale source, GLB integrity, furniture
                    bounds, collision, and doorway checks so you can see exactly what was verified.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* CTA Section */}
        <section className="py-20 bg-gray-800">
          <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
            <h2 className="text-3xl font-bold text-white mb-4">
              Ready to Reconstruct a Property?
            </h2>
            <p className="text-xl text-gray-400 mb-8">
              Bring a floor plan. The pipeline handles validation, reconstruction, automatic
              furnishing, navigation checks, and the interactive walkthrough.
            </p>
            <Link
              href="/create-tour"
              className="bg-blue-600 text-white px-8 py-3 rounded-lg font-medium hover:bg-blue-700 transition-colors inline-block"
            >
              Start a Reconstruction
            </Link>
          </div>
        </section>
      </main>

      <footer className="bg-gray-900 border-t border-gray-800 py-8">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center text-gray-400">
          <p>&copy; 2026 RealEstAI. All rights reserved.</p>
        </div>
      </footer>
    </div>
  )
}
