import { NextResponse, type NextRequest } from 'next/server'

export async function middleware(request: NextRequest) {
  // Note: This is a simplified middleware for Phase 1
  // In production, implement proper JWT validation with Supabase
  
  // For now, allow all routes - authentication is handled client-side
  const response = NextResponse.next({
    request,
  })

  return response
}

export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)',
  ],
}
