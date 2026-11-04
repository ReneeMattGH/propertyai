import Link from 'next/link'
import Navigation from '@/components/Navigation'

export default function NotFound() {
  return (
    <div className="min-h-screen bg-ivory">
      <Navigation />
      <main className="shell py-24">
        <div className="card mx-auto max-w-xl px-8 py-12 text-center">
          <p className="eyebrow">Not found</p>
          <h1 className="mt-2 text-[30px] leading-tight">This page does not exist</h1>
          <p className="mt-3 text-sm leading-6 text-stone">
            The studio has no route at this address. It may have been renamed, or the link was
            typed by hand.
          </p>
          <div className="mt-7 flex flex-wrap justify-center gap-2.5">
            <Link href="/dashboard" className="btn btn-primary">
              Go to dashboard
            </Link>
            <Link href="/requests" className="btn btn-secondary">
              Open the request queue
            </Link>
          </div>
        </div>
      </main>
    </div>
  )
}
