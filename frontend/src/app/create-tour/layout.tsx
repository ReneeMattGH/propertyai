import type { ReactNode } from 'react'
import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'New walkthrough' }

export default function RouteLayout({ children }: { children: ReactNode }) {
  return children
}
